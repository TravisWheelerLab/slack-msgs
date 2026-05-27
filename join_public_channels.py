import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Initialize the Slack client with your bot token
client = WebClient(token=os.environ['TOKEN'])

def join_all_public_channels():
    try:
        # List all public channels
        response = client.conversations_list(types="public_channel")
        channels = response['channels']

        # Iterate over each channel and join
        for channel in channels:
            channel_id = channel['id']
            try:
                client.conversations_join(channel=channel_id)
                print(f"Joined channel: {channel['name']}")
            except SlackApiError as e:
                # Handle error if bot is already in the channel or another error occurs
                if e.response['error'] == 'already_in_channel':
                    print(f"Already in channel: {channel['name']}")
                else:
                    print(f"Error joining channel {channel['name']}: {e.response['error']}")

    except SlackApiError as e:
        print(f"Error listing channels: {e.response['error']}")

if __name__ == "__main__":
    join_all_public_channels()
