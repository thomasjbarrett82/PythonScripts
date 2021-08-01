import os
import re
import logging
from pathlib import Path
import statistics
import pickle
import time 

from itunesLibrary import library
#import itunesLibrary ## https://github.com/scholnicks/itunes-library/
from plexapi.myplex import PlexServer ## https://python-plexapi.readthedocs.io/en/latest/index.html
from plexapi.exceptions import NotFound

### available iTunes item attributes:
# Track ID, Size, Total Time, Disc Number, Disc Count, Track Number, Track Count, Year, BPM, Date Modified, Date Added, Bit Rate, Sample Rate, Play Count, Play Date
# Play Date UTC, Skip Count, Skip Date, Rating, Loved, Persistent ID, Track Type, File Folder Count, Library Folder Count, Name, Artist, Album Artist, Composer, Album
# Grouping, Genre, Kind, Comments, Sort Name, Sort Artist, Sort Composer, Work, Location

def xstr(s):
    return "" if s == None else s

def MakeKeyStringSafe(key):
    return re.sub(r'[^a-zA-Z0-9]', '', key)

def GenerateTrackKey(artist, album, title):
    tmpKey = artist + album + title
    tmpKey = MakeKeyStringSafe(tmpKey)
    return tmpKey

if __name__ == '__main__':
    ## set up logging
    if os.path.exists("SyncITunesPlaylistsToPlex.log"):
        os.remove("SyncITunesPlaylistsToPlex.log")
    logging.basicConfig(level=logging.INFO
        , filename="SyncITunesPlaylistsToPlex.log"
        , format="%(asctime)s %(levelname)s: %(message)s"
        , datefmt="%Y-%m-%d %H:%M:%S"
        , encoding="UTF-16")

    try:
        logging.info("Starting load of iTunes and Plex libraries")

        ## iTunes Library
        path = os.path.join(str(Path.home()),"Music\iTunes\iTunes Music Library.xml")   ## path for actual iTunes library xml, assumes default Windows location
#        ## debug only - use pickle to save binary copy for faster access during development
#        pickleFile = "itl.p"
#        expiry = 60 * 60
#        epochTime = int(time.time()) # now
#        # generate pickled file if stale or doesn't exist
#        if not os.path.isfile(pickleFile) or os.path.getmtime(pickleFile) + expiry < epochTime:
#            itl_source = library.parse(path)
#            pickle.dump(itl_source, open(pickleFile, "wb"))
#            logging.info("Debug: Pickle library refreshed.")
#        iLibrary = pickle.load(open(pickleFile, "rb"))
        iLibrary = library.parse(path)
        iTunesPlaylists = iLibrary.playlists
        logging.info("Loaded iTunes")

        ## Plex library
        baseurl = '<url>'
        token = '<token>'
        plexLibrary = PlexServer(baseurl, token)
        logging.info("Loaded Plex library")

        ## get all music tracks from Plex
        allTracks = plexLibrary.library.section("Music").searchTracks(sort="titleSort") # assumes the Plex music library is called "Music"

        ## load music tracks into dictionary for fast matching with iTunes tracks
        ## takes a while to initially load, but is much faster than loops
        allTracksDict = {}
        for track in allTracks:
            # parsing out actual title for each item because Plex stores it in different ways
            trackArtist = track.grandparentTitle if xstr(track.originalTitle) == "" else track.originalTitle # originalTitle used for Various Artists albums
            trackAlbum = track.parentTitle
            trackTitle = track.titleSort if xstr(track.title) == "" else track.title # not sure why track.title is sometimes empty 
            trackKey = GenerateTrackKey(trackArtist, trackAlbum, trackTitle) # matching special characters was a pain, stripping everything but alphanumeric to make the key easy
            allTracksDict[trackKey] = track

        ## get all artists from Plex
        allArtists = plexLibrary.library.section("Music").searchArtists(sort="titleSort")

        ## get all albums from Plex
        allAlbums = plexLibrary.library.section("Music").searchAlbums(sort="titleSort")

        ## sync each user-generated, non-"z" playlist from iTunes to Plex
        for iPlaylist in iTunesPlaylists:
            if not iPlaylist.title[0] == "z" and not iPlaylist.is_distinguished() and not iPlaylist.title == "Library" and not iPlaylist.title == "Recently Added" and not iPlaylist.title == "1 Star" and not iPlaylist.title == "2 Stars": # excluding some specific playlists
                logging.info("Starting synching " + iPlaylist.title)
                # delete Plex playlist if it exists
                # faster to delete and re-create playlists than deleting playlist items and adding back in (more API calls)
                try:
                    pToDelete = plexLibrary.playlist(iPlaylist.title)
                    pToDelete.delete()
                except NotFound as e: 
                    logging.info("Playlist " + iPlaylist.title + " not found, creating new Plex playlist.")
                # create playlist and add iTunes items
                itemsToAdd = []
                for item in iPlaylist:
                    itemKey = GenerateTrackKey(item.artist, item.album, item.title)
                    tmp = allTracksDict.get(itemKey, None)
                    if not tmp == None:
                        itemsToAdd.append(tmp)
                    else: 
                        logging.warning(str(itemKey) + " not found in Plex allTracks, skipping item.")
                if not itemsToAdd == []:
                    plexLibrary.createPlaylist(title = iPlaylist.title, items = itemsToAdd)
        logging.info("Finished synching " + iPlaylist.title)

        ## sync track ratings from iTunes to Plex
        logging.info("Starting synching iTunes track ratings to Plex")
        allPlaylists = iter(iTunesPlaylists)
        allItunesTracks = next(x for x in allPlaylists if x.title == "Music")
        for item in allItunesTracks:
            itemKey = GenerateTrackKey(item.artist, item.album, item.title)
            tmp = allTracksDict.get(itemKey, None)
            tmpRating = (int(item.getItunesAttribute("Rating")) / 10) if not item.getItunesAttribute("Rating") == None else 0
            if not tmp == None and tmpRating > 0:
                tmpArgs = {"userRating.value": tmpRating}
                tmp.edit(**tmpArgs)
            else: 
                logging.warning(str(itemKey) + " not found in Plex allTracks or iTunes not rated, skipping item.")
        logging.info("Finished synching iTunes track ratings to Plex")

        ## sync album ratings from iTunes to Plex
        ## get average rating for each Plex album & artist
        logging.info("Starting synching iTunes album ratings to Plex")
        for album in allAlbums:
            tmpAlbumTracks = [i for i in iLibrary.items if i.album == album.title and i.artist == album.parentTitle]
            if len(tmpAlbumTracks) > 0: 
                iAlbumRatingsList = [int(t.getItunesAttribute("Rating") if not t.getItunesAttribute("Rating") == None else 0) for t in tmpAlbumTracks]
                if not iAlbumRatingsList == None:
                    iAlbumRating = statistics.mean(iAlbumRatingsList)
                    iAlbumRating = round((iAlbumRating/10), 1)
                    #logging.info(album.title + " by " + album.parentTitle + " rating: " + str(iAlbumRating))
                    tmpArgs = {"userRating.value": iAlbumRating}
                    album.edit(**tmpArgs)
        logging.info("Finished synching iTunes album ratings to Plex")

        ## sync artist ratings from iTunes to Plex
        ## get average rating for each Plex artist
        logging.info("Starting synching iTunes artist ratings to Plex")
        for artist in allArtists:
            tmpArtistTracks = iLibrary.getItemsForArtist(artist.title)
            if len(tmpArtistTracks) > 0:
                iArtistRatingsList = [int(t.getItunesAttribute("Rating") if not t.getItunesAttribute("Rating") == None else 0) for t in tmpArtistTracks]
                if not iArtistRatingsList == None:
                    iArtistRating = statistics.mean(iArtistRatingsList)
                    iArtistRating = round((iArtistRating/10), 1)
                    tmpArgs = {"userRating.value": iArtistRating}
                    artist.edit(**tmpArgs)
        logging.info("Finished synching iTunes artist ratings to Plex")

    except Exception as e:
        logging.exception("Failure!")
