'''
Code based on https://gist.github.com/benoit-cty/a5855dea9a4b7af03f1f53c07ee48d3c

Script to archive Slack messages from a channel list.
You have to create a Slack Bot and invite to private channels.
View https://github.com/docmarionum1/slack-archive-bot for how to configure your account.

This will download all channels in the workspace but will only be successful on channels
where the bot app is added to the channel's "Apps" integration

Exports adhere to Slack's official export format
'''

# Import WebClient from Python SDK (github.com/slackapi/python-slack-sdk)
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import json, os, urllib.request, urllib.parse

# don't put the bot token where it winds up in github
TOKEN = os.environ['TOKEN']
FILE_TOKEN = os.environ.get('TOKEN')  # file access token via public dump
DOWNLOAD = os.environ.get('DOWNLOAD')
TIMESTAMPFILE = "lastdownload.txt"

client = WebClient(token=TOKEN)
indent = 0

def slack_list(field, info, operation, **dargs):
  # Most WebClient methods are paginated, returning the first n results
  # along with a "next_cursor" pointer to fetch the rest.
  print(f'{" " * indent}Fetching {info or field}...')
  try:
    items = []
    cursor = None
    while True:
      result = operation(cursor=cursor, **dargs)
      items += result[field]
      if 'response_metadata' not in result: break
      cursor = result['response_metadata']['next_cursor']
      if not cursor: break
      print(f'{" " * indent}  Fetching more...')
    print(f'{" " * indent}  Fetched {len(items)} {field}')
  except SlackApiError as e:
    print("ERROR USING CONVERSATION: {}".format(e))
  return items

def all_channels():
  return slack_list('channels', 'all channels',
    client.conversations_list, types='public_channel, private_channel')

def all_channel_members(channel):
  return slack_list('members', f'all members in channel {channel["name"]}',
    client.conversations_members, channel=channel['id'])

def all_channel_messages(channel, oldest=None):
    kwargs = {'channel': channel['id']}
    if oldest:
        kwargs['oldest'] = oldest

    main_messages = slack_list('messages', f'all messages from channel {channel["name"]}',
                               client.conversations_history, **kwargs)

    all_messages = []

    seen_thread_ts = set()  # To track seen thread timestamps

    for message in main_messages:
        thread_ts = message.get('thread_ts')
        if thread_ts and thread_ts not in seen_thread_ts:
            seen_thread_ts.add(thread_ts)
            thread_messages = slack_list('messages', f'threaded messages for {thread_ts} in channel {channel["name"]}',
                                         client.conversations_replies, channel=channel['id'], ts=thread_ts)
            all_messages.extend(thread_messages)
        else:
            all_messages.append(message)

    return all_messages

def all_users():
  return slack_list('members', 'all users', client.users_list)

def save_json(data, filename):
  print('  Saving to', filename)
  os.makedirs(os.path.dirname(filename), mode=0o700, exist_ok=True)
  with open(filename, 'w') as outfile:
    json.dump(data, outfile, indent=2)

def load_last_timestamp(channel_name):
    filename = f'backup/{channel_name}/last_timestamp.txt'
    try:
        with open(filename, 'r') as file:
            return file.read().strip()
    except FileNotFoundError:
        return None

def save_last_timestamp(channel_name, timestamp):
    filename = f'backup/{channel_name}/last_timestamp.txt'
    os.makedirs(os.path.dirname(filename), mode=0o700, exist_ok=True)
    with open(filename, 'w') as file:
        file.write(timestamp)

def backup_channel(channel):
  try:
    # Fetch messages with a specific timestamp (oldest)
    last_timestamp = load_last_timestamp(channel['name'])
    all_messages = all_channel_messages(channel, oldest=last_timestamp)

    if all_messages:
            # Load existing messages if the file exists
            backup_filename = f'backup/{channel["name"]}/all.json'
            existing_messages = []
            if os.path.exists(backup_filename):
                with open(backup_filename, 'r') as existing_file:
                    existing_messages = json.load(existing_file)

            # Filter out messages that were already backed up
            new_messages = [msg for msg in all_messages if msg not in existing_messages]

            # Append new messages to the existing messages
            existing_messages += new_messages

            # Save the combined messages back to the file
            with open(backup_filename, 'w') as outfile:
                json.dump(existing_messages, outfile, indent=2)

            # Save the last timestamp
            last_message = new_messages[-1] if new_messages else all_messages[-1]
            if 'ts' in last_message:
                save_last_timestamp(channel['name'], last_message['ts'])

    # Rewrite private URLs to have token, like Slack's public dump
    filenames = {'all.json'}  # avoid overwriting json
    count = 0
    for message in all_messages:
      if 'files' in message:
        for file in message['files']:
          count += 1
          for key, value in list(file.items()):
            if (key.startswith('url_private') or key.startswith('thumb')) \
               and isinstance(value, str) and value.startswith('https://'):
              if FILE_TOKEN:
                file[key] = value + '?t=' + FILE_TOKEN
              if DOWNLOAD and not key.endswith('_download'):
                filename = os.path.basename(urllib.parse.urlparse(value).path)
                if filename in filenames:
                  i = 0
                  base, ext = os.path.splitext(filename)
                  def rewrite():
                    return base + '_' + str(i) + ext
                  while rewrite() in filenames:
                    i += 1
                  filename = rewrite()
                filenames.add(filename)
                # https://api.slack.com/types/file#authentication
                with urllib.request.urlopen(urllib.request.Request(value,
                       headers={'Authorization': 'Bearer ' + TOKEN})) as infile:
                  with open(f'backup/{channel["name"]}/{filename}', 'wb') as outfile:
                    outfile.write(infile.read())
                file[key + '_file'] = f'{channel["name"]}/{filename}'
    verbs = []
    if DOWNLOAD: 
       verbs.append('Downloaded')
    if FILE_TOKEN: 
       verbs.append('Linked')
    if verbs: print(f'  {" & ".join(verbs)} {count} files from messages in {channel["name"]}.')

    if count and FILE_TOKEN:
      save_json(all_messages, f'backup/{channel["name"]}/all.json')

  except SlackApiError as e:
      print("Error using conversation: {}".format(e))
  except FileNotFoundError:
      print(f'No existing all.json file found for channel {channel["name"]}. Creating a new one.')
      save_json(all_messages, f'backup/{channel["name"]}/all.json')
      last_message = all_messages[-1]
      if 'ts' in last_message:
          save_last_timestamp(channel['name'], last_message['ts'])

def backup_all_channels():
  global indent
  channels = all_channels()
  indent += 2
  for channel in channels:
    channel['members'] = all_channel_members(channel)
  indent -= 2
  save_json(channels, 'backup/channels.json')
  for channel in channels:
    backup_channel(channel)

def backup_all_users():
  users = all_users()
  save_json(users, 'backup/users.json')

if __name__ == "__main__":
  backup_all_users()
  backup_all_channels()