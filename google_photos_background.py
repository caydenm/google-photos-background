#!/usr/bin/python3

import argparse
import asyncio
import json
import pickle
import platform
import random
import subprocess
from os import environ, getuid, listdir, mkdir
from os.path import abspath, dirname, exists, isabs, isfile, join

import requests
from crontab import CronTab
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from questionary import prompt

ALBUM_LIST_FILENAME = '.albums.json'
SCRIPT_NAME = 'google_photos_background.py'

OSX_SCRIPT_SET_PHOTOS_FOLDER = '/usr/bin/osascript<<END tell application \"System Events\" to set pictures folder of every desktop to \"${TARGET_DIR}\" end tell END'
OSX_SCRIPT_SET_PERIOD = '/usr/bin/osascript<<END tell application \"System Events\" to set change interval of every desktop to 300 end tell END'
OSX_SCRIPT_SET_RANDOM = '/usr/bin/osascript<<END tell application \"System Events\" to set random order of every desktop to true end tell END'


def get_albums(google_photos):
    album_results = google_photos.albums().list(pageSize=50).execute()
    albums = album_results.get('albums', [])
    next_page_token = album_results.get('nextPageToken', '')

    while (next_page_token != ''):
        
        album_results =  google_photos.albums().list(
            pageSize=50, pageToken=next_page_token).execute()
        albums += album_results.get('albums', [])
        next_page_token = album_results.get('nextPageToken', '')

    return albums

def generate_choices(albums, existing_choices):
    choices = []
    print(existing_choices)
    for album in albums:
        album_id = album.get('id', '')
        if (album_id in existing_choices):
            print('found an existing selected item')
            choices.append({
                'name': album.get('title', ''),
                'checked': True,
                'value': album.get('id', ''),
            })
        else:
            choices.append({
                'name': album.get('title', ''),
                'value': album.get('id', ''),
                'checked': False,
            })

    questions = [
        {
        'type': 'checkbox',
        'message': 'Select albums',
        'name': 'albums',
        'choices': choices 
        }
    ]

    return questions

async def download_photos(album_id, google_photos, args):
    download_count = 0
    items = []
    results = google_photos.mediaItems().search(
                body={"albumId": album_id, "pageSize": 50}).execute()
    items += results.get('mediaItems', [])
    next_page_token = results.get('nextPageToken', '')

    while (next_page_token != ''):
        results = google_photos.mediaItems().search(
            body={"albumId": album_id, "pageSize": 50, "pageToken": next_page_token}).execute()
        items += results.get('mediaItems', [])
        next_page_token = results.get('nextPageToken', '')

    # Convert to a set so we remove any duplicates so we don't refetch
    tasks = []
    print ('Downloading ' + str(len(items)) +' photos. This may take a while!')
    for item in items:
        if "image/" in item['mimeType']:
            print("Downloading " + item['filename'])

            filenames = listdir(args.folder)
            if (item['filename'] in filenames):
                print('Already downloaded ' + item['filename'])
                continue

            # Add in the download param to get full quality photos
            tasks.append(asyncio.create_task(download_and_save_image(item, args)))
            download_count = download_count + 1
        else:
            print("Skipping " + item['filename'])

    for task in tasks:
        await task


async def download_and_save_image(item, args):
    response = requests.get(item['baseUrl'] + '=d')
    try:
        with open(join(args.folder, item['filename']), 'wb') as f:
            f.write(response.content)
            print("Downloaded " + item['filename'])
    except:
        print('Something went wrong with ' + item['filename'])        

def download_albums(album_list, google_photos, args):
    for album_id in album_list:
        asyncio.run(download_photos(album_id, google_photos, args))

def get_api_client():
    SCOPES = 'https://www.googleapis.com/auth/photoslibrary.readonly'

    creds = None
    if exists((join(dirname(__file__), 'token.pickle'))):
        with open((join(dirname(__file__), 'token.pickle')), 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print('No credentials starting oauth flow')
            flow = InstalledAppFlow.from_client_secrets_file(
                join(dirname(__file__), 'client_id.json'),
                scopes=[SCOPES])
            #flow = client.flow_from_clientsecrets(join(dirname(__file__), 'client_id.json'), SCOPES)
            creds = flow.run_local_server()
            with open((join(dirname(__file__), 'token.pickle')), 'wb') as token:
                pickle.dump(creds, token)
    #http=creds.authorize(Http())
    google_photos = build('photoslibrary', 'v1', credentials = creds)
    return google_photos

def save_albums(answers):
    with open((join(dirname(__file__), ALBUM_LIST_FILENAME)), 'w') as outfile:
        json.dump(answers['albums'], outfile)
        print('Saved albums list as '+ ALBUM_LIST_FILENAME)

def read_albums():
    print('Reading saved albums from disk')
    try:
        with open(join(dirname(__file__), ALBUM_LIST_FILENAME)) as json_file:
                album_list = json.load(json_file)
                return album_list
    except:
        return []

def get_random_photo_from_folder(args):
    print('Picking a random photo')
    filenames = listdir(args.folder)
    filename = random.choice(filenames)

    while not isfile(join(args.folder, filename)):
        filename = random.choice(filenames)

    print(filename)
    
    return join(args.folder, filename)


def setup_update_of_albums(args):
    print('Albums will be checked every hour for new photos')
    # set for current user
    cron = CronTab(user=True)
    job = cron.new(command='/"'+ join(abspath(dirname(__file__)), SCRIPT_NAME) + ' --update ' + abspath(args.folder)+ '/"')
    job.hour.every(1)
    cron.write()

def setup_change_background(args):
    if platform.system() == 'Linux':
        # set for current user
        cron = CronTab(user=True)
        job = cron.new(command='/"'+ join(abspath(dirname(__file__)), SCRIPT_NAME) + ' --change-background ' + abspath(args.folder) + '/"')
        job.minute.every(5)
        cron.write()
        change_background(get_random_photo_from_folder(args))
    elif platform.system() == 'Darwin':
        subprocess.Popen(OSX_SCRIPT_SET_PHOTOS_FOLDER.format(TARGET_DIR=args.folder), shell=True)
        subprocess.Popen(OSX_SCRIPT_SET_PERIOD, shell=True)
        subprocess.Popen(OSX_SCRIPT_SET_RANDOM, shell=True)
    else:
        print('Unsupported OS')

def change_background(photo_path):
    print('Changing the background' + photo_path)
    if platform.system() == 'Linux':
        # Cron runs this without having a dbus session address which is required to change gsettings
        # We relies on a standardized dbus address based on the current users uid
        # This appears to work on Ubuntu
        my_env = environ.copy()
        my_env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/' + str(getuid()) + '/bus'
        proc = subprocess.Popen('/usr/bin/gsettings set org.gnome.desktop.background picture-uri file:///' + photo_path, shell=True, env=my_env)
        (out, err) = proc.communicate()
        print(out)
        print(err)

def __main__():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update",dest='update', action='store_const', const=True)
    parser.add_argument("--change-background",dest='change', action='store_const', const=True)
    parser.add_argument("folder")
    args = parser.parse_args()
    print (args)

    if not isabs(args.folder):
        args.folder = abspath(args.folder)
    if not exists(args.folder):
        mkdir(args.folder)
        args.folder = abspath(args.folder)

    print('Photos folder: ' + args.folder)

    google_photos = get_api_client()

    existing_choices = read_albums()

    if (not args.update and not args.change):

        albums = get_albums(google_photos)

        questions = generate_choices(albums, existing_choices)

        # Make a list of which albums we want to sync 

        answers = prompt(questions)
        # We will need to save this list somewhere

        save_albums(answers)

        # We have a list, now we need to download all the photos in each of those albums
        download_albums(answers['albums'], google_photos, args)

        setup_update_of_albums(args)

        setup_change_background(args)
    elif args.change:
        change_background(get_random_photo_from_folder(args))
    else:
        print('Updating..')
        if (existing_choices != []):
            download_albums(existing_choices, google_photos, args)
        else:
            print('No albums to update')

    # Now we have all the photos downloaded, we need to need the wallpaper
    # We will need to work out what OS we are on or have it told to us?

    # For Linux a cron job will need to be set to call this script again
    # to update the photos 


__main__()
