from TwitterAPI import TwitterAPI
import logging
import time
import json
import sys
from datetime import datetime, timedelta, date
from apscheduler.schedulers.blocking import BlockingScheduler
import random
import os


def get_logger():
    #Creates the logger object that is used for logging in the file

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    #Create log outputs
    fh = logging.FileHandler('log')
    ch = logging.StreamHandler()

    #Log format
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    #Set logging format
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    #Set level per output
    fh.setLevel(logging.DEBUG)
    ch.setLevel(logging.INFO)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


#The logger object
logger = get_logger()


class Config:
    """Class that contains all  config variables. It loads user values from a json file """

    # Default values
    consumer_key = None
    consumer_secret = None
    access_token_key = None
    access_token_secret = None
    daily_tweets = 300
    scan_update_time = 5400
    clear_queue_time = 43200
    min_posts_queue = 60
    rate_limit_update_time = 60
    blocked_users_update_time = 300
    min_ratelimit = 10
    min_ratelimit_retweet = 20
    min_ratelimit_search = 40
    max_follows = 1950
    search_queries = ["RT to win", "Retweet and win"]
    follow_keywords = [" follow ", " follower "]
    fav_keywords = [" fav ", " favorite "]

    @staticmethod
    def load(filename):
        # Load our configuration from the JSON file.
        with open(filename) as data_file:
            data = json.load(data_file)

        for key, value in data.items():
            #!Fixme:
            #Hacky code because the corresponding keys in config file use - instead of _
            key = key.replace('-', '_')
            setattr(Config, key, value)


# Don't edit these unless you know what you're doing.
api = None #Its initialized if this is main
post_list = list()
ratelimit = [999, 999, 100]
ratelimit_search = [999, 999, 100]


class IgnoreList(list):
    """
    A list like object that loads contents from a file and everything that is appended here gets also
    appended in the file
    """

    def __init__(self, filename):
        self.filename = filename
        self.load_file()

    def append(self, p_object):
        self.append_file(p_object)
        super().append(p_object)

    def load_file(self):
        with open(self.filename, 'a+') as f:
            f.seek(0)
            self.extend(int(x) for x in f.read().splitlines())

    def append_file(self, p_object):
        with open(self.filename, 'a+') as f:
            f.write(str(p_object) + '\n')

ignore_list = None


def encode_timestamp(timestamp):
	return str(timestamp).replace(" ", "+").replace(":", "%3A")


def random_time(start, end):
	sec_diff = int((end-start).total_seconds())
	secs_to_add = random.randint(0, sec_diff)
	return start + timedelta(seconds=secs_to_add)


def get_daily_tweets_random_times(n, start, end):
	times = []
	for i in range(0, Config.daily_tweets):
	    times.append(random_time(start, end))
	times.sort()
	return times


# Schedule random times over the course of the day to call UpdateQueue, 
# giving the application the appearance of manual interaction.
# Number of tweets per day can be defined in config - daily-tweets.
def RandomTimes():
	# we need to parse today's state to properly
	# schedule the tweet times
	dadate = datetime.now()
	year = dadate.year
	month = dadate.month
	day = dadate.day

	# the lower bound
	lower_bound = datetime(year, month, day, 1, 0, 0)
	logger.info("[{}] - the lower bound is {}".format(datetime.now(), lower_bound))

	# the upper bound
	upper_bound = datetime(year, month, day, 23, 0, 0)
	logger.info("[{}] - the upper bound is {}".format(datetime.now(), upper_bound))

	times = get_daily_tweets_random_times(Config.daily_tweets, lower_bound, upper_bound)
	logger.info("[{}] - Received {} times to schedule".format(datetime.now(),
	                                                         len(times)))

	for ind, atime in enumerate(times):
	    if ind == (Config.daily_tweets-1):
	        scheduler.add_job(UpdateQueue, 'date', run_date=atime)
	        logger.info("[{}] - added last task at {}".format(datetime.now(),
	                                                         atime))
	    else:
	        scheduler.add_job(UpdateQueue, 'date', run_date=atime)
	        logger.info("[{}] - added task at {}".format(datetime.now(),
	                                                     atime))


def CheckError(r):
    r = r.json()
    if 'errors' in r:
        logger.error("We got an error message: {0} Code: {1})".format(r['errors'][0]['message'],
                                                                      r['errors'][0]['code']))
        # sys.exit(r['errors'][0]['code'])


def CheckRateLimit():

    global ratelimit
    global ratelimit_search

    if ratelimit[2] < Config.min_ratelimit:
        logger.warn("Ratelimit too low -> Cooldown ({}%)".format(ratelimit[2]))
        time.sleep(30)

    r = api.request('application/rate_limit_status').json()

    for res_family in r['resources']:
        for res in r['resources'][res_family]:
            limit = r['resources'][res_family][res]['limit']
            remaining = r['resources'][res_family][res]['remaining']
            percent = float(remaining) / float(limit) * 100

            if res == "/search/tweets":
                ratelimit_search = [limit, remaining, percent]

            if res == "/application/rate_limit_status":
                ratelimit = [limit, remaining, percent]

            #print(res_family + " -> " + res + ": " + str(percent))
            if percent < 5.0:
                message = "{0} Rate Limit-> {1}: {2} !!! <5% Emergency exit !!!".format(res_family, res, percent)
                logger.critical(message)
                sys.exit(message)
            elif percent < 30.0:
                logger.warn("{0} Rate Limit-> {1}: {2} !!! <30% alert !!!".format(res_family, res, percent))
            elif percent < 70.0:
                logger.info("{0} Rate Limit-> {1}: {2}".format(res_family, res, percent))

# Update the Retweet queue (this prevents too many retweets happening at once.)


def UpdateQueue():

    logger.info("=== CHECKING RETWEET QUEUE ===")

    logger.info("Queue length: {}".format(len(post_list)))

    if len(post_list) > 0:

        if not ratelimit[2] < Config.min_ratelimit_retweet:

            post = post_list[0]
            if not 'errors' in post:
                logger.info("Retweeting: {0} {1}".format(post['id'], post['text'].encode('utf8')))

                r = api.request('statuses/show/:%d' % post['id']).json()
                if 'errors' in r:
                    logger.error("We got an error message: {0} Code: {1}".format(r['errors'][0]['message'],
                                                                                 r['errors'][0]['code']))
                    post_list.pop(0)
                else:
                    user_item = r['user']
                    user_id = user_item['id']

                    if not user_id in ignore_list:

                        r = api.request('statuses/retweet/:{0}'.format(post['id']))
                        CheckError(r)
                        post_list.pop(0)

                        if not 'errors' in r.json():

                        	CheckForFollowRequest(post)
                        	CheckForFavoriteRequest(post)

                    else:
                        post_list.pop(0)
                        logger.info("Blocked user's tweet skipped")
            else:
                post_list.pop(0)
                logger.error("We got an error message: {0} Code: {1}".format(post['errors'][0]['message'],
                                                                             post['errors'][0]['code']))
        else:
            logger.info("Ratelimit at {0}% -> pausing retweets".format(ratelimit[2]))


# Check if a post requires you to follow the user.
# Be careful with this function! Twitter may write ban your application
# for following too aggressively
def CheckForFollowRequest(item):
    text = item['text']
    if any(x in text.lower() for x in Config.follow_keywords):
        RemoveOldestFollow()
        try:
            r = api.request('friendships/create', {'screen_name': item['retweeted_status']['user']['screen_name']})
            CheckError(r)
            logger.info("Follow: {0}".format(item['retweeted_status']['user']['screen_name']))
        except:
            user = item['user']
            screen_name = user['screen_name']
            r = api.request('friendships/create', {'screen_name': screen_name})
            CheckError(r)
            logger.info("Follow: {0}".format(screen_name))

# FIFO - Every new follow should result in the oldest follow being removed.


def RemoveOldestFollow():
    friends = list()
    for id in api.request('friends/ids'):
        friends.append(id)

    oldest_friend = friends[-1]

    if len(friends) > Config.max_follows:

        r = api.request('friendships/destroy', {'user_id': oldest_friend})

        if r.status_code == 200:
            status = r.json()
            logger.info('Unfollowed: {0}'.format(status['screen_name']))

    else:
        logger.info("No friends unfollowed")

    del friends
    del oldest_friend

# Check if a post requires you to favorite the tweet.
# Be careful with this function! Twitter may write ban your application
# for favoriting too aggressively


def CheckForFavoriteRequest(item):
    text = item['text']

    if any(x in text.lower() for x in Config.fav_keywords):
        try:
            r = api.request('favorites/create', {'id': item['retweeted_status']['id']})
            CheckError(r)
            logger.info("Favorite: {0}".format(item['retweeted_status']['id']))
        except:
            r = api.request('favorites/create', {'id': item['id']})
            CheckError(r)
            logger.info("Favorite: {0}".format(item['id']))

# Clear the post list queue in order to avoid a buildup of old posts


def ClearQueue():
    post_list_length = len(post_list)

    if post_list_length > Config.min_posts_queue:
        del post_list[:post_list_length - Config.min_posts_queue]
        logger.info("===THE QUEUE HAS BEEN CLEARED===")

# Check list of blocked users and add to ignore list


def CheckBlockedUsers():

    if not ratelimit_search[2] < Config.min_ratelimit_search:

        for b in api.request('blocks/ids'):
            if not b in ignore_list:
                ignore_list.append(b)
                logger.info("Blocked user {0} added to ignore list".format(b))
    else:

        logger.warn("Update blocked users skipped! Queue: {0} Ratelimit: {1}/{2} ({3}%)".format(len(post_list),
                                                                                                ratelimit_search[1],
                                                                                                ratelimit_search[0],
                                                                                                ratelimit_search[2]))

# Scan for new contests, but not too often because of the rate limit.


def ScanForContests():

    global ratelimit_search

    if not ratelimit_search[2] < Config.min_ratelimit_search:

        logger.info("=== SCANNING FOR NEW CONTESTS ===")

        for search_query in Config.search_queries:

            logger.info("Getting new results for: {0}".format(search_query))

            try:
                r = api.request( 'search/tweets', {'q': search_query, 'result_type': "mixed", 'count': 50})
                CheckError(r)
                c = 0

                for item in r:

                    c = c + 1
                    user_item = item['user']
                    screen_name = user_item['screen_name']
                    text = item['text']
                    text = text.replace("\n", "")
                    id = item['id']
                    original_id = id

                    if 'retweeted_status' in item:

                        original_item = item['retweeted_status']
                        original_id = original_item['id']
                        original_user_item = original_item['user']
                        original_screen_name = original_user_item['screen_name']

                        if not original_id in ignore_list:

                            if not original_user_item['id'] in ignore_list:

                                post_list.append(original_item)

                                logger.info("{0} - {1} retweeting {2} - {3} : {4}".format(id, screen_name, original_id,
                                                                                          original_screen_name,text))

                                ignore_list.append(original_id)

                            else:

                                logger.info("{0} ignored {1} blocked and in ignore list".format(id,
                                                                                                original_screen_name))
                        else:

                            logger.info("{0} ignored {1} in ignore list".format(id, original_id))

                    else:

                        if not id in ignore_list:

                            if not user_item['id'] in ignore_list:

                                post_list.append(item)

                                logger.info("{0} - {1} : {2}".format(id, screen_name, text))
                                ignore_list.append(id)

                            else:

                                logger.info("{0} ignored {1} blocked user in ignore list".format(id, screen_name))
                        else:

                            logger.info("{0} in ignore list".format(id))

                logger.info("Got {0} results".format(c))

            except Exception as e:
                logger.exception("Could not connect to TwitterAPI - are your credentials correct?")

    else:

        logger.warn("Search skipped! Queue: {0} Ratelimit: {1}/{2} ({3}%)".format(len(post_list),
                                                                                  ratelimit_search[1],
                                                                                  ratelimit_search[0],
                                                                                  ratelimit_search[2]))

if __name__ == '__main__':

	#Load config
	Config.load('config.json')

	#Initialize twitter api
	api = TwitterAPI(
    	Config.consumer_key,
    	Config.consumer_secret,
    	Config.access_token_key,
    	Config.access_token_secret)

	#Initialize ignorelist
	ignore_list = IgnoreList("ignorelist")

	#Initialize scheduler
	scheduler = BlockingScheduler()

	#First run
	RandomTimes()
	ClearQueue()
	CheckRateLimit()
	CheckBlockedUsers()
	ScanForContests()

	scheduler.add_job(RandomTimes, 'interval', hours=24)
	scheduler.add_job(ClearQueue, 'interval', seconds=Config.clear_queue_time)
	scheduler.add_job(CheckRateLimit, 'interval', seconds=Config.rate_limit_update_time)
	scheduler.add_job(CheckBlockedUsers, 'interval', seconds=Config.blocked_users_update_time)
	scheduler.add_job(ScanForContests, 'interval', seconds=Config.scan_update_time)

	try:
		scheduler.start()
	except (KeyboardInterrupt, SystemExit):
		pass
