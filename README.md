# Scrapeler
Scrapeler is a python-based scraper for harvesting images (usually anime). Scrapeler is developed in Python 3 but attempts are made to make sure it works in Python 2.

Currently supported sites:
* [Gelbooru](http://gelbooru.com/)

__If Scrapeler does not work for you, leave an issue on the repo with a detailed dump of what happened. No repro = No fix.__

Scrapeler requires the following libraries:
* beautifulsoup4
* pillow
* requests
* fake-useragent
* html5lib

## Set up
* Download the repo through whatever git magic you prefer.
* Open a command prompt
* Navigate to the downloaded folder and run "pip install -r requirements.txt"
* You're done!

Earlier versions of the libraries listed in requirements *might* work, but we make no promises.


## How the dink do I use this garbage?
Using Scrapeler is easy:

1. Navigate to the site you want to farm for images.
2. Narrow down the search to something you want to farm, like 'pokemon rating:safe -digimon' 
3. Translate the gelbooru search syntax (pokemon rating:safe, -digimon) to Scrapeler's query syntax (pokemon rating:safe -e digimon)
4. Hit enter and go! By default, Scrapeler will generate a path to save images to.


# FAQ
##### Does this work in Python 3?
Yes. Scrapeler is developed primarily in Python 3.

##### Does this work in Python 2?
Attempts are made to make sure Scrapeler works with Python 2.7+, but that's not a guarantee. It should work.

##### Does this work on Windows?
Scrapeler should work on Windows 8.1 and above. (Never tested on 7 or below)

##### Does this work on Linux/Mac?
Probably, but no guarantees.

##### I don't want to save repeats.
Scrapeler scans a directory before saving to it and does not save over anything with the same filename. You can also specify a directory to not save repeats from with the --blacklist argument. Because of this, Scrapeler works best if you leave the filenames as the defaults they were downloaded with.

##### I do want to save repeats.
Either save images to a different directory when you scrape, or rename the files.

##### ...in the same directory?
No can do.

##### This thing sucks because it can't find images of muh waifu!
Get a more popular waifu. (Also make sure you spelled everything correctly and understand the arguments used to start Scrapeler.)

##### What's with the @syntax?
Specifying '@' before an argument tells scrapeler to read the contents of that file and use those instead. Scrapeler calls these 'response files'.

##### Why would I ever use --scanonly?
It's there as a way to give you more information and try to avoid images you might not want. Say you look for 'pokemon' and 'zubat' comes up a lot in the results. Zubat brings back uncomfortable memories, but now you know it's there and can exclude or filter that tag before doing a query that saves anything.

##### Why would I use --filter when I can use --exclude?
That's really up to you, but the filter option can be used to great effect with response files. If you're never interested in a several particular tags, such as digimon or sword_art_online, you can specify those in a file and use that file with all queries. Then, even if you're searching for 'cirno', Scrapeler knows not to save files with those tags, and the booru has no idea.
There's also a limit to the amount of tags booru's let you search by, and that includes negative tags, which the --exclude argument uses.