#!/usr/bin/env python
# -*- coding: utf-8 -*-
# __BEGIN_LICENSE__
#  Copyright (c) 2009-2013, United States Government as represented by the
#  Administrator of the National Aeronautics and Space Administration. All
#  rights reserved.
#
#  The NGT platform is licensed under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance with the
#  License. You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# __END_LICENSE__


import sys, os, glob, optparse, re, shutil, subprocess, string, time

import json, urllib2, requests, argparse

import IrgStringFunctions

import apiclient
from apiclient import discovery
import httplib2
from oauth2client import client
from oauth2client import file as oauth2client_file
from oauth2client import tools

# Code numbers used to identify which sensor is being loaded
SENSOR_TYPE_HiRISE = 0
SENSOR_TYPE_HRSC   = 1
SENSOR_TYPE_CTX    = 2
SENSOR_TYPE_THEMIS = 3

def man(option, opt, value, parser):
    print >>sys.stderr, parser.usage
    print >>sys.stderr, '''\
Tool for uploading raster images to Google Maps Engine
'''
    sys.exit()

class Usage(Exception):
    def __init__(self, msg):
        self.msg = msg

# TODO: Don't reload these every time!
def loadKeys():
    '''Load the authorization keys'''
    keyPath = os.path.join(sys.path[0], 'keys.txt')
    
    f = open(keyPath, 'r')
    lines = f.readlines()
    f.close()

    # Authorization codes
    API_KEY       = IrgStringFunctions.getLineAfterText(lines[0], '=').strip()
    CLIENT_ID     = IrgStringFunctions.getLineAfterText(lines[1], '=').strip()
    CLIENT_SECRET = IrgStringFunctions.getLineAfterText(lines[2], '=').strip()
    PROJECT_ID    = IrgStringFunctions.getLineAfterText(lines[3], '=').strip()
   
    return (API_KEY, CLIENT_ID, CLIENT_SECRET, PROJECT_ID)

def printErrorInfo(desiredCode, receivedCode, errorText):
    '''Prints a short message if the error is known, long otherwise'''

    if receivedCode == 401:
        print('Error: Unauthorized access!')
    elif receivedCode == 403:
        print('Warning: Rate limit exceeded!')
    elif receivedCode == 404:
        print('Error: The specified entity does not exist!')
    elif receivedCode == 500:
        print('Warning: Internal server error!')
    elif receivedCode == 503:
        print('Warning: Service backend error!')
    elif receivedCode != desiredCode:
        print errorText # Did not recognize the code, print the full error text


def getProjectsInfo(bearerToken):
    """Returns a list of the user's projects"""

    # Send request for information on this asset
    url         = 'https://www.googleapis.com/mapsengine/v1/projects/'
    tokenString = 'Bearer '+bearerToken
    headers     = {'Authorization': tokenString}
    response    = requests.get(url, headers=headers)
    
    print response.text

    # Check status code
    DESIRED_CODE = 200
    printErrorInfo(DESIRED_CODE, response.status_code, response.text)
    if response.status_code != DESIRED_CODE:
        return (False, response.status_code)

    return (True, response.status_code)


def queryUploadedFile(bearerToken, assetId = '04070367133797133737-15079892155897256865'):
    """Fetches information about a file has already been uploaded into Maps Engine"""

    # Send request for information on this asset
    url         = 'https://www.googleapis.com/mapsengine/v1/rasters/' + assetId
    tokenString = 'Bearer '+bearerToken
    headers     = {'Authorization': tokenString}
    response    = requests.get(url, headers=headers)
    
    #print response.text

    # Check status code
    DESIRED_CODE = 200
    printErrorInfo(DESIRED_CODE, response.status_code, response.text)
    if response.status_code != DESIRED_CODE:
        return (False, response.status_code)

    # Convert to dictionary format    
    jsonDict = json.loads(response.text)
    return (True, jsonDict)


# TODO: Is there a way to check for a file without knowing the asset ID?
def checkIfFileIsLoaded(bearerToken, assetId = '04070367133797133737-15079892155897256865'):
    """Determine if a file has already been uploaded into Maps Engine"""

    (result, info) = queryUploadedFile(bearerToken, assetId)

    if not result: # Query failed, return error info
        return (result, info)

   
    # TODO: More accurate check?
    # Check if all files are loaded and processing is finished    
    jsonDict = info
    status = True
    for f in jsonDict['files']:
        if (f['uploadStatus'] != 'complete'):
            status = False
    if jsonDict['processingStatus'] != 'complete':
        status = False

    return status, 200
    

def deleteUploadedAsset(bearerToken, assetId):
    """Deletes an asset has already been uploaded into Maps Engine"""

    # Send request for information on this asset
    url         = 'https://www.googleapis.com/mapsengine/v1/rasters/' + assetId.strip()
    tokenString = 'Bearer '+bearerToken
    headers     = {'Authorization': tokenString}
    response    = requests.delete(url, headers=headers)

    #print response.text
    print response.status_code

    # Check status code
    DESIRED_CODE = 200
    printErrorInfo(DESIRED_CODE, response.status_code, response.text)
    if response.status_code != DESIRED_CODE:
        return (False, response.status_code)

    # Convert to dictionary format    
    jsonDict = json.loads(response.text)
    return (True, jsonDict)

def deleteAssetsInList(bearerToken, assetListPath):
    '''Deletes all of the assets listed in the CSV file'''

    # Set up log files to record successes and failures
    inputBasePath  = os.path.splitext(assetListPath)[0]
    successLogPath = inputBasePath + '_out_success.csv'
    failureLogPath = inputBasePath + '_out_failure.csv'
    print 'Success log = ' + successLogPath
    print 'Failure log = ' + failureLogPath

    # Reset the connection if we fail this many times in a row
    FAIL_COUNT_LIMIT = 10

    # Read the asset ID from each line of the file, then delete it!
    numDeleted = 0
    numFailed  = 0
    successiveFailCount = 0
    f    = open(assetListPath, 'r')
    sLog = open(successLogPath, 'w')
    fLog = open(failureLogPath, 'w')
    for line in f:
        parts   = line.split(',')
        name    = parts[0]
        assetId = parts[1]

        # TODO: Update this with more accurate status reporting!

        print 'Deleting asset: ' + line
        try:
            (result, info) = deleteUploadedAsset(bearerToken, assetId)
        except: # Handle unknown errors
            result = False
        if result:
            sLog.write(line)
            numDeleted += 1
            successiveFailCount = 0
            break # DEBUG!!!!!
        else:
            fLog.write(line)
            print 'Failed to delete asset!'
            numFailed += 1
            successiveFailCount += 1
        time.sleep(1.0)

        if successiveFailCount >= FAIL_COUNT_LIMIT:
            print 'Refreshing network connection...'
            try:
                bearerToken = authorize()
            except:
                pass
            successiveFailCount = 0
    
    # Clean up and report results
    f.close()
    sLog.close()
    fLog.close()
    print 'Successfully deleted: ' + str(numDeleted)
    print 'Failed to delete:     ' + str(numFailed)
    return 0



# TODO: Deprecate this function!
def getRasterList(bearerToken):
    """Gets a list of uploaded files - used special case to recover from a lost DB!"""

    # This only works if all of the files have been manually loaded into a mosaic in Maps Engine!

    # TODO: Generalize this!
    ctxAssetId    = '04070367133797133737-11575601353866955997'
    setiProjectId = '04070367133797133737'
    assetId       = ctxAssetId

    url = 'https://www.googleapis.com/mapsengine/v1/rasterCollections/' + assetId + '/rasters?projectId=' + setiProjectId
    tokenString = 'Bearer '+bearerToken

    # We will have to make multiple sequential requests due to return count limits.
    gotEntireList = False
    nextPageToken = None
    assetList = []

    while (not gotEntireList):

        # Send request for information on this asset
        headers = {'Authorization': tokenString}
        if nextPageToken:           
            payload = {'pageToken': nextPageToken}
            response = requests.get(url, headers=headers, params=payload)
        else:
            response = requests.get(url, headers=headers)       

        # Check status code
        DESIRED_CODE = 200
        printErrorInfo(DESIRED_CODE, response.status_code, response.text)
        if response.status_code != DESIRED_CODE:
            return []
        
        # Get all the information from each of these files
        jsonDict  = json.loads(response.text)
        
        print 'Got ' + str(len(jsonDict['rasters'])) + ' results'
        status = True
        for f in jsonDict['rasters']:

            print '\n===========================\n'
            print f

            # A few of these fields will need to be reformatted to fit in the database
            assetInfo = dict()
            assetInfo['assetID']    = f['id']
            assetInfo['uploadTime'] = f['creationTime']
            assetInfo['name']       = f['name']
            assetInfo['minLat']     = f['bbox'][0]
            assetInfo['minLon']     = f['bbox'][1]
            assetInfo['maxLat']     = f['bbox'][2]
            assetInfo['maxLon']     = f['bbox'][3]
            #TODO: Handle bbox error when failed data is hit!

            assetList.append(assetInfo)

        # Get the next page token, otherwise we are finished.
        if 'nextPageToken' in jsonDict:
            nextPageToken = jsonDict['nextPageToken']
        else:
            gotEntireList = True
       
    return assetList


def findAllRasterUploads(bearerToken, cacheFolder, tag):
    """Gets a list of all uploaded files with the given tag"""
    # TODO: Add more flexibility
    # TODO: Change things so the cache does not have to be cleared when files are changed!

    setiProjectId = '04070367133797133737'

    url = 'https://www.googleapis.com/mapsengine/v1/rasters?projectId='+setiProjectId+'&tags='+tag#+'&key={YOUR_API_KEY}'

    # We will have to make multiple sequential requests due to return count limits.
    gotEntireList = False
    nextPageToken = None
    assetList     = []

    FAIL_COUNT_LIMIT = 10
    pageNum   = 0
    failCount = 0
    while (not gotEntireList): # Keep fetching more files until we have the entire list

        # Local cache path for this request
        cachePath = os.path.join(cacheFolder, str(pageNum)+'.json')
        thisText = ''

        if os.path.exists(cachePath): # Read from disk
            print 'Reading cached file ' + cachePath
            
            # Read from cache file
            f = open(cachePath, 'r')
            thisText = f.read()
            f.close()
            
        else:
            print 'Submitting web request'
            
            # Send request for information on this asset
            tokenString = 'Bearer '+bearerToken
            headers     = {'Authorization': tokenString}
            if nextPageToken:           
                payload = {'pageToken': nextPageToken}
                response = requests.get(url, headers=headers, params=payload)
            else:
                response = requests.get(url, headers=headers)       
    
            # Check status code
            DESIRED_CODE = 200
            printErrorInfo(DESIRED_CODE, response.status_code, response.text)
            if response.status_code != DESIRED_CODE:
                print 'Retrieval error, trying again'
                time.sleep(1.5)
                failCount += 1
                if failCount >= FAIL_COUNT_LIMIT:
                    failCount   = 0
                    bearerToken = authorize()
                continue
            
            # Write to cache file
            f = open(cachePath, 'w')
            f.write(response.text)
            f.close()
            thisText = response.text
        
        pageNum += 1
        
        # Get all the information from each of these files
        jsonDict  = json.loads(thisText)
        
        print 'Got ' + str(len(jsonDict['rasters'])) + ' results'
        status = True
        for f in jsonDict['rasters']:

            # Record only fields of interest
            assetInfo = dict()
            assetInfo['assetID']          = f['id']
            assetInfo['uploadTime']       = f['creationTime']
            assetInfo['name']             = f['name']
            assetInfo['processingStatus'] = f['processingStatus']
            
            assetList.append(assetInfo)

        # Get the next page token, otherwise we are finished.
        if 'nextPageToken' in jsonDict:
            nextPageToken = jsonDict['nextPageToken']
        else:
            gotEntireList = True
            
    return assetList


#--------------------------------------------------------------------------------

def getCredentials(redo=False):

    # Parse OAuth command line arguments
    parser = argparse.ArgumentParser(parents=[tools.argparser])
    #flags = parser.parse_args()
    flags, unknown = parser.parse_known_args()
    
    #print 'Looking for cached credentials'
    # Check if we already have a file with OAuth credentials
    storage = oauth2client_file.Storage('mapsengine.dat')
    credentials = storage.get()
    
    API_KEY, CLIENT_ID, CLIENT_SECRET, PROJECT_ID = loadKeys()
    
    if credentials is None or credentials.invalid or redo:
        print 'Getting credentials'
        # Start local server, redirect user to authentication page, receive OAuth
        # credentials on the local server, and store credentials in file
        flow = client.OAuth2WebServerFlow(
                            client_id=CLIENT_ID,
                            client_secret=CLIENT_SECRET,
                            response_type='code',
                            scope='https://www.googleapis.com/auth/mapsengine',
                            user_agent='Google-MapsEngineApiSample/1.0',
                            redirect_uri='urn:ietf:wg:oauth:2.0:oob',
                            access_type='offline',
                            approval_prompt='auto') # Change to 'auto'
        credentials = tools.run_flow(flow, storage, flags)
      
    return credentials

def authorize(redo=False):

    credentials = getCredentials(redo)    
    
    #jsonCredentials = credentials.to_json()
    #jsonDict = json.loads(jsonCredentials)
    #for key, value in jsonDict.iteritems():
    #    print key + ' ---> ' + str(value)
    
    # Set up discovery with authorized credentials
    http = credentials.authorize(httplib2.Http())
    
    API_KEY, CLIENT_ID, CLIENT_SECRET, PROJECT_ID = loadKeys()

    #print 'Setting up Maps Engine API service.'
    service = apiclient.discovery.build('mapsengine', 'v1', http=http, developerKey=API_KEY)

    # It is not clear why but adding this pointless code fixed the authorization problems!
    #print '=================='    
    # Read the location of every Feature in a Table.
    #print 'Testing dummy request'
    features = service.tables().features()
    request = features.list(id='12421761926155747447-06672618218968397709',
                            maxResults=10, version='published')
    response = request.execute()
    #for feature in response['features']:
    #  print feature['geometry']['coordinates']
    #print '---------------------'

    jsonCredentials = credentials.to_json()
    jsonDict = json.loads(jsonCredentials)
    #for key, value in jsonDict.iteritems():
    #    print key + ' ---> ' + str(value)
    
    bearerToken = jsonDict['access_token']
    #print bearerToken
    return bearerToken
    

## Read the location of every Feature in draft version of Table.
#features = service.tables().features()
#request = features.list(id=TABLE_ID)
#while request is not None:
#  resource = request.execute()
#  for feature in resource['features']:
#    print feature['geometry']['coordinates']
#
#  # Is there an additional page of features to load?
#  request = features.list_next(request, resource)

def createRasterAsset(bearerToken, inputPathList, sensorType, acqTime=None, extraTags=None):
    
    API_KEY, CLIENT_ID, CLIENT_SECRET, PROJECT_ID = loadKeys()

    url = 'https://www.googleapis.com/mapsengine/v1/rasters/upload'
    
    # The first file in the list is used for metadata
    mainInputPath = inputPathList[0]
    justFilename = os.path.basename(mainInputPath)
    
    # Set up list of files to be uploaded
    filesField = []
    for f in inputPathList:
        filesField.append({ "filename": os.path.basename(f) })
    
    
    # Set up common metadata
    data = ({"projectId": PROJECT_ID,  # REQUIRED, taken from Maps Engine URL}
#             "files": [ # REQUIRED
#                      { "filename": justFilename }
#                     ],
             "files": filesField, # REQUIRED
             #"acquisitionTime": {
             #   "start": acqTime,
             #   "end":   acqTime,
             #   "precision": "second"
             #   },
             "draftAccessList": "Map Editors", # REQUIRED
             "maskType": "autoMask"})

    if acqTime: # Add the timestamp if it was passed in
        data["acquisitionTime"] = {"start": acqTime,
                                   #"end":   acqTime,
                                   "precision": "second"
                                  }

    # Sensor specific metadata
    if sensorType == SENSOR_TYPE_HiRISE:
        data["name"]        = 'HiRISE_'+justFilename  # REQUIRED
        data["description"] = "HiRISE map projected RDR data"
        data["attribution"] = "NASA Public Domain" # REQUIRED
        data["tags"]        = ["Mars", "MRO", "HiRISE"]
    elif sensorType == SENSOR_TYPE_HRSC:
        data["name"]        = 'HRSC_'+justFilename  # REQUIRED
        data["description"] = "HRSC map projected RDR data"
        data["attribution"] = "NASA Public Domain" # REQUIRED
        data["tags"]        = ["Mars", "MEX", "HRSC"]
    elif sensorType == SENSOR_TYPE_CTX:
        data["name"]        = 'CTX_'+justFilename  # REQUIRED
        data["description"] = "CTX map projected RDR data"
        data["attribution"] = "NASA Public Domain" # REQUIRED
        data["tags"]        = ["Mars", "MRO", "CTX"]
    elif sensorType == SENSOR_TYPE_THEMIS:
        data["name"]        = 'THEMIS_'+justFilename  # REQUIRED
        data["description"] = "THEMIS map projected data"
        data["attribution"] = "NASA Public Domain" # REQUIRED
        data["tags"]        = ["Mars", "MO", "THEMIS"]
    else:
        raise Exception('Unrecognized sensor type!')

    if extraTags: # Add extra user tags if they were passed in
        data["tags"] += extraTags

    # Test code for uploading other data
#        data["name"]        = 'UAVSAR_'+justFilename  # REQUIRED
#        data["description"] = "UAV SAR map projected data"
#        data["attribution"] = "Copyright flood detection" # REQUIRED
#        data["tags"]        = ["UAVSAR", "RADAR"]

        
    #print(data)
    tokenString = 'Bearer '+bearerToken
    headers = {'Authorization': tokenString,
               'Content-Type': 'application/json'}
    #print(headers)
    
    response = requests.post(url, data=json.dumps(data), headers=headers)
    
    # Check status code
    DESIRED_CODE = 200
    printErrorInfo(DESIRED_CODE, response.status_code, response.text)   
    if response.status_code != DESIRED_CODE:
        return (False, response.status_code)
    
    jsonDict = json.loads(response.text)
    #for key, value in jsonDict.iteritems():
    #    print key + ' ---> ' + str(value)
       
    # Return the asset ID from the response
    return (True, jsonDict['id'])
    


def uploadFile(bearerToken, assetId, filename):

    # Check input image
    if not os.path.exists(filename):
        raise Exception('Input image file ' + filename + ' is missing!')
    imageSizeBytes = os.path.getsize(filename)
    
    # Get the file extension tag to use
    #ext = os.path.splitext(filename)[1]
    #if (ext.lower() == '.jp2'):
    if ('.jp2' in filename.lower()):
        contentString = 'image/jpg2'
    else: # Default to geotiff type
        contentString = 'image/tiff'
    
    # Set up POST request
    justFilename = os.path.basename(filename)
    url = 'https://www.googleapis.com/upload/mapsengine/v1/rasters/'+str(assetId)+'/files?filename='+justFilename
    tokenString = 'Bearer '+bearerToken
    headers = {'Authorization':  tokenString,
               'Content-Type':   contentString,
               'Content-Length': str(imageSizeBytes)}

    #print headers
    #print url
    #print filename

    # Submit the post request with file data
#    fileList = {'file': open(filename, 'rb')}
#    response = requests.post(url, headers=headers, files=fileList)
 
    with open(filename, 'rb') as f:
        response = requests.post(url, headers=headers, data=f) 
    
    # Check status code
    DESIRED_CODE = 204
    printErrorInfo(DESIRED_CODE, response.status_code, response.text)
    
    # Check response status code
    if response.status_code != 204:
        return False
    print 'File upload started successfully!'
    return True
 
    # To check upload progress:
    # GET https://www.googleapis.com/mapsengine/v1/rasters/{raster_ID}
    # Authorization: Bearer {token}


def main(argsIn):

    print ('#################################################################################')
    print ("Running mapsEngineUpload.py")

    #try:
    #try:
    usage = "usage: mapsEngineUpload.py <input images> [--manual]\n  "
    parser = optparse.OptionParser(usage=usage)

    parser.add_option("--sensor", type="int", dest="sensor", default=0,
                              help="Which sensor? (HiRISE=0, HRSC=1, CTX=2, THEMIS=3).")

    parser.add_option("--acqTime", dest="acqTime", default="",
                              help="Pass in the acquisition time of the image in 'YYYY-MM-DDTHH:MM:SSZ' format.")

    parser.add_option("--tag", dest="tag", default=None,
                              help="Pass in an additional metadata tag.")

    parser.add_option("--checkAsset", dest="checkAsset", default="",
                              help="Query the status of an asset.")

    parser.add_option('--deleteAssets', dest='deleteAssets', action='store_true',
                      help='Read in a list of assets and delete them all.')

    parser.add_option("--manual", action="callback", callback=man,
                      help="Read the manual.")
    
    (options, args) = parser.parse_args(argsIn)

    #print args

    if len(args) < 1: # DEBUG
        options.inputPath = '/home/smcmich1/data/production/NAC_DTM_M151318807_M181974094/results/output-DEM.tif'
        #options.inputPath = '/home/smcmich1/data/google/mapsengine-cmd-line-sample/PSP_001427_1820_RED.JP2'
    else:
        # Load a list of input files and make sure they all exist
        options.inputPathList = []
        for f in args:
            options.inputPathList.append(f)
            if (not options.checkAsset) and (not os.path.exists(f)):
                raise Exception('Input file '+ f +'does not exist!')
    
    #except(optparse.OptionError, msg):
    #    raise Usage(msg)
    
    # For now we only support a single extra tag
    extraTags = None
    if options.tag:
        extraTags = [options.tag]
    
    startTime = time.time()

    # Get server authorization
    bearerToken = authorize()
    print 'Got bearer token'
    
    
    if options.deleteAssets: # Call desired function
        return deleteAssetsInList(bearerToken, options.inputPathList[0])


    MAX_NUM_RETRIES = 20  # Max number of times to retry (in case server is busy)
    SLEEP_TIME      = 2.5 # Time to wait between retries (Google handles only one operation/second)
   
    if options.checkAsset: # Query asset status by ID

        for i in range(1,MAX_NUM_RETRIES):
            success, statusCode = checkIfFileIsLoaded(bearerToken, options.checkAsset)
            print success
            print statusCode
            if success:
                return 1
            else:
                print 'sleeping...'
                time.sleep(SLEEP_TIME)
        return 0
       
    else: # Upload the image
              
        # Create empty raster asset request
        for i in range(1,MAX_NUM_RETRIES):
            success, assetId = createRasterAsset(bearerToken, options.inputPathList,
                                                 options.sensor, options.acqTime, extraTags)
            if success:
                break
            else: # Wait for more than a second before trying again
                time.sleep(SLEEP_TIME)
        #if not success:
        #    print 'Refreshing access token...'
        #    bearerToken = authorize(True)
        #    (success, assetId) = createRasterAsset(bearerToken, options.inputPath, options.sensor)
        if not success:
            raise Exception('Could not get access token!')
        
        print 'Created asset ID ' + str(assetId)
        
        # Load each file associated with the asset
        for inputPath in options.inputPathList:
            for i in range(1,MAX_NUM_RETRIES):
                success = uploadFile(bearerToken, assetId, inputPath)
                if success:
                    break
                else: # Wait for more than a second before trying again
                    time.sleep(SLEEP_TIME)

    
    endTime = time.time()

    #print("Finished in " + str(endTime - startTime) + " seconds.")
    #print('#################################################################################')
    return assetId

    #except(Usage, err):
    #    print(err)
    #    #print(>>sys.stderr, err.msg)
    #    return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
