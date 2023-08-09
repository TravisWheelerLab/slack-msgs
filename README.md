# slack-msgs
Backup Slack messages

Exports in a format such that it can be read with other projects like slack-export-viewer

https://github.com/hfaran/slack-export-viewer

To use

* The bot has to be added to all channels you wish it to backup
  * You can `/invite @botname` or,
  * Go into the channel details and under Integrations, add as an app

* Add the bot token to the `run_slack_backup.sh` file
* Run `run_slack_backup.sh`
* A backup subdirectory is created containing the message data organized by channel name

If you have slack-export-viewer installed you can
* cd into the backup directory
* run `slack-export-viewer -z ./ -p 5001`  (You can try omitting the `-p 5001` switch; the default port is 5000 but that port doesn't seem to work for me on a Mac)
* your web browser should open up and you now will be able to browse around the channels and messages