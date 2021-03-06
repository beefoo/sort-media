# -*- coding: utf-8 -*-

import argparse
import csv
import inspect
import json
import math
import os
from pprint import pprint
import sys

# add parent directory to sys path to import relative modules
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
parentdir = os.path.dirname(parentdir)
sys.path.insert(0,parentdir)

from lib.io_utils import *

# input
parser = argparse.ArgumentParser()
parser.add_argument('-query', dest="QUERY", default="collection:(prelingerhomemovies) AND mediatype:(movies)", help="Query. See reference: https://archive.org/advancedsearch.php")
parser.add_argument('-keys', dest="RETURN_KEYS", default="date,description,identifier,item_size,publicdate,subject,title,creator,licenseurl", help="List of keys to return")
parser.add_argument('-sort', dest="SORT_BY", default="downloads desc", help="Sort string")
parser.add_argument('-format', dest="FORMAT", default=".mp4", help="Derivative to retrieve")
parser.add_argument('-multi', dest="MULTI_FORMAT", action="store_true", help="Download multiple istances of the same format?")
parser.add_argument('-rows', dest="ROWS", default=100, type=int, help="Rows per page")
parser.add_argument('-out', dest="OUTPUT_FILE", default="tmp/internet_archive_metadata.csv", help="CSV output file")
parser.add_argument('-overwrite', dest="OVERWRITE", action="store_true", help="Overwrite existing data?")
args = parser.parse_args()

# Parse arguments
QUERY = args.QUERY.strip().replace(" ", "+")
RETURN_KEYS = args.RETURN_KEYS.strip().split(",")
SORT_BY = args.SORT_BY.strip().replace(" ", "+")
FORMAT = args.FORMAT if len(args.FORMAT) > 0 else False
MULTI_FORMAT = args.MULTI_FORMAT
ROWS = args.ROWS
OUTPUT_FILE = args.OUTPUT_FILE
OVERWRITE = args.OVERWRITE

# Make sure output dirs exist
makeDirectories(OUTPUT_FILE)

# Get existing data
rows = []
if os.path.isfile(OUTPUT_FILE) and not OVERWRITE:
    fieldNames, rows = readCsv(OUTPUT_FILE)
rowCount = len(rows)

returnKeysString = "".join(["&fl[]="+k for k in RETURN_KEYS])
url = "https://archive.org/advancedsearch.php?q=%s%s&sort[]=%s&rows=%s&output=json&save=yes" % (QUERY, returnKeysString, SORT_BY, ROWS)
page = 1

firstPage = getJSONFromURL(url + "&page=%s" % page)
numFound = firstPage["response"]["numFound"]
pages = int(math.ceil(1.0*numFound/ROWS))
print("Found %s results in %s pages" % (numFound, pages))

if rowCount >= numFound and not OVERWRITE:
    print("Already found enough existing data, exiting...")
    sys.exit()

if rowCount > 0:
    pagesComplete = int(math.floor(1.0*rowCount/ROWS))
    page = pagesComplete + 1
    # get rid of any partial page
    rows = rows[:pagesComplete*ROWS]
    print("Partial file found. Starting at page %s" % page)
else:
    rows = firstPage["response"]["docs"]

writeCsv(OUTPUT_FILE, rows, RETURN_KEYS)

while page < pages:
    page += 1
    data = getJSONFromURL(url + "&page=%s" % page)
    docs = data["response"]["docs"]
    writeCsv(OUTPUT_FILE, docs, RETURN_KEYS, append=True)
    rows += docs

# Also download derivative data
if FORMAT:
    RETURN_KEYS.append("filename")

    fileRows = []
    for i, row in enumerate(rows):
        id = row["identifier"]
        metadataUrl = "https://archive.org/metadata/%s" % id
        data = getJSONFromURL(metadataUrl)
        if not data or "files" not in data:
            print("No valid derivative format found in %s" % metadataUrl)
            continue
        files = [f for f in data["files"] if "name" in f and f["name"].endswith(FORMAT)]
        files = sorted(files, key=lambda k: int(k['size']), reverse=True)
        if len(files) <= 0:
            print("No valid derivative format found in %s" % metadataUrl)
            continue

        if not MULTI_FORMAT:
            files = [files[0]]

        for f in files:
            rcopy = row.copy()
            rcopy["filename"] = f["name"]
            fileRows.append(rcopy)

        sys.stdout.write('\r')
        sys.stdout.write("%s%%" % roundInt(1.0*i/(len(rows)-1)*100))
        sys.stdout.flush()

    writeCsv(OUTPUT_FILE, fileRows, RETURN_KEYS)
