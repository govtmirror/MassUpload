
import os
import sys
import copy
import re
import json
import numpy
import multiprocessing
import functools

import IrgGeoFunctions

import MosaicUtilities
import mosaicTileManager


# TODO: Move to a general file
def projCoordToPixelCoord(x, y, geoInfo):
    '''Converts from projected coordinates into pixel coordinates using
       the results from getImageGeoInfo()'''

    xOffset = x - geoInfo['projection_bounds'][0]
    yOffset = y - geoInfo['projection_bounds'][3]
    
    column = xOffset / geoInfo['pixel_size'][0]
    row    = yOffset / geoInfo['pixel_size'][1]
    
    return (column, row)

# TODO: Move this somewhere?
def getTilePrefix(tileRow, tileCol):
    '''Return a standard string representation of a tile index'''
    return (str(tileRow)+'_'+str(tileCol))

def splitImage(imagePath, outputFolder, tileSize=512, force=False):
        '''Splits up an image into a grid of tiles and returns all the tile paths'''
                
        filename     = os.path.basename(imagePath)[:-4] # Strip extension
        outputPrefix = os.path.join(outputFolder, filename + '_tile_')
        
        ## Compute the bounding box for each tile
        #inputImageSize    = IrgGeoFunctions.getImageSize(imagePath)
        #numTilesX = ceil(inputImageSize[0] / tileSize)
        #numTilesY = ceil(inputImageSize[1] / tileSize)
        #
        ## Generate each of the tiles using GDAL
        #outputTileInfoList = []
        #for r in range(0,numTilesY):
        #    for c in range(0,numTilesX):
        #        
        #        # Get the pixel ROI for this tile
        #        # - TODO: This should be handled by a class!
        #        minCol = c*tileHeight
        #        minRow = r*tileWidth
        #        width  = tileWidth
        #        height = tileHeight
        #        if (minCol + width ) > inputImageSize[0]: width  = inputImageSize[0] - minCol
        #        if (minRow + height) > inputImageSize[1]: height = inputImageSize[1] - minRow
        #        totalNumPixels  = height*width
        #        
        #        # Generate the tile
        #        thisPixelRoi = ('%d %d %d %d' % (minCol, minRow, width, height))
        #        thisTilePath = outputPrefix + str(r) +'_'+ str(c) + '.tif'
        #        cmd = 'gdal_translate -srcwin ' + thisPixelRoi +' '+ imagePath +' '+ thisTilePath
        #        cmdRunner(cmd, thisTilePath, force)
        #
        #        # Set up a dictionary entry for this tile
        #        blackPixelCount = countBlackPixels(thisTilePath)
        #        validPercentage = 1.0 - (blackPixelCount / totalNumPixels)
        #        
        #        # Load all the information into the dictionary
        #        thisTileInfo = {'path'        : thisTilePath,
        #                        'tileRow'     : r,
        #                        'tileCol'     : c,
        #                        'pixelRow'    : minRow,
        #                        'pixelCol'    : minCol,
        #                        'height'      : height,
        #                        'width'       : width,
        #                        'percentValid': validPercentage,
        #                        'prefix'      : getTilePrefix(r, c)
        #                       }
        #        outputTileInfoList.append(thisTileInfo)
        #        
        #return outputTileInfoList 
        #
        # Skip tile creation if the first tile is present
        # - May need to make the decision smarter later on
        firstTilePath = outputPrefix + '0_0.tif'
        if not os.path.exists(firstTilePath):
        
            # This tile size is in the warped image (HRSC) resolution
            # TODO: Use gdal to do this so that the regions are preserved?
            cmd = ('convert %s -crop %dx%d -set filename:tile "%%[fx:page.y/%d]_%%[fx:page.x/%d]" +repage +adjoin "%s%%[filename:tile].tif"'
                      % (imagePath, tileSize, tileSize, tileSize, tileSize, outputPrefix))
            print cmd
            os.system(cmd)
        
        # Build the list of output files
        outputTileInfoList = []
        for f in os.listdir(outputFolder):
            if ('_tile_' not in f) or ('json' in f): # Skip metadata files and any junk
                continue
            thisPath = os.path.join(outputFolder, f)
            thisMetadataPath = thisPath + '_metadata.json' # Path to record the metadata to
            
            # If the metadata is saved, just reload it.
            if os.path.exists(thisMetadataPath):
                with open(thisMetadataPath, 'r') as f:
                    thisTileInfo = json.load(f)
                    
            else: # Otherwise we have to recompute everything!
            
                # Figure out the position of the tile
                numbers  =  re.findall(r"[\d']+", f) # Extract all numbers from the file name
                tileRow  = int(numbers[3]) # In the tile grid
                tileCol  = int(numbers[4])
                pixelRow = tileRow * tileSize # In pixel coordinates relative to the original image
                pixelCol = tileCol * tileSize
                
                # TODO: Cache this information!
                # Get other tile information
                width, height   = IrgGeoFunctions.getImageSize(thisPath)
                totalNumPixels  = height*width
                blackPixelCount = MosaicUtilities.countBlackPixels(thisPath)
                validPercentage = 1.0 - (float(blackPixelCount) / float(totalNumPixels))
                
                thisTileInfo = {'path'        : thisPath,
                                'tileRow'     : tileRow,
                                'tileCol'     : tileCol,
                                'pixelRow'    : pixelRow,
                                'pixelCol'    : pixelCol,
                                'heightPixels': height,
                                'widthPixels' : width,
                                'percentValid': validPercentage,
                                'prefix'      : getTilePrefix(tileRow, tileCol)
                               }
            
                # Cache the metadata to disk so we don't have to recompute
                with open(thisMetadataPath, 'w') as f:
                    json.dump(thisTileInfo, f)
                
            outputTileInfoList.append(thisTileInfo)
        # End of loop through files   
    
        return outputTileInfoList 



#-----------------------------------------------------------------------


# The order that HRSC channel images are stored and their names in a list
NUM_HRSC_CHANNELS = 5
HRSC_RED   = 0
HRSC_GREEN = 1
HRSC_BLUE  = 2
HRSC_NIR   = 3
HRSC_NADIR = 4
CHANNEL_STRINGS = ['red', 'green', 'blue', 'nir', 'nadir']

HRSC_HIGH_RES_TILE_SIZE = 1024

class HrscImage():
    '''
       Class to manage an input HRSC image.
       
       In addition to loading and tiling the HRSC image components,
       this class handles some low resolution adjustment to match the
       base map.
       
       This class does not do any operations that depend on specific
       high resolution basemap data.
    '''
    
    # TODO: Add setup/teardown functions that remove large image data but keep the small outputs on disk
    
    def __init__(self, sourceFileInfoDict, outputFolder, basemapInstance, force=False, threadPool=None):
        '''Set up all the low resolution HRSC products.'''
        
        setName = sourceFileInfoDict['setName']
        print 'Initializing HRSC image: ' + setName
        
        # Initialize some values to empty in case they are accessed prematurely
        self._tileDict = None #
        
        # Set up some paths
        self._setName    = setName
        self._threadPool = threadPool
        self._outputFolder        = outputFolder
        self._basemapInstance     = basemapInstance
        self._hrscBasePathOut     = os.path.join(outputFolder, setName)
        self._tileFolder          = self._hrscBasePathOut + '_tiles'
        self._lowResMaskPath      = self._hrscBasePathOut + '_low_res_mask.tif'
        self._highResMaskPath     = self._hrscBasePathOut + '_high_res_mask.tif'
        self._brightnessGainsPath = self._hrscBasePathOut + '_brightness_gains.csv'
        self._basemapCropPath     = self._hrscBasePathOut + '_local_cropped_basemap.tif' # A crop of the basemap used in several places
        self._basemapGrayCropPath = self._hrscBasePathOut + '_local_gray_cropped_basemap.tif'
        #self._colorPairPath       = self._hrscBasePathOut + '_low_res_color_pairs.csv'
        self._basemapSpatialRegistrationPath       = self._hrscBasePathOut + '_low_res_spatial_transform_basemap.csv' # Transform to the low res basemap
        self._croppedRegionSpatialRegistrationPath = self._hrscBasePathOut + '_cropped_region_spatial_transform.csv'  # Transform to cropped region of low res basemap
        self._highResSpatialRegistrationPath       = self._hrscBasePathOut + '_high_res_spatial_transform_basemap.csv'
        self._lowResSpatialCroppedRegistrationPath = self._hrscBasePathOut + '_low_res_cropped_spatial_transform.csv'
        
        # Record input parameters
        self._basemapColorPath = basemapInstance.getColorBasemapPath() # Path to the color low res entire base map
        #self._basemapGrayPath  = basemapInstance.getGrayBasemapPath()  # Path to the grayscale low res entire base map
        
        # Get full list of input paths from the input dictionary
        self._inputHrscPaths = sourceFileInfoDict['allChannelPaths']
        
        
        print 'Generating low res image copies...'
        
        # Generate a copy of each input HRSC channel at the low basemap resolution
        self._lowResWarpedPaths = [self._warpToProjection(path, outputFolder, '_basemap_res',
                                                          basemapInstance.getLowResMpp(), force)
                                   for path in self._inputHrscPaths]
        
        # Build up a string containing all the low res paths for convenience
        self._lowResPathString = ''
        for path in self._lowResWarpedPaths:
            self._lowResPathString += path + ' '
            
        print 'Generating low resolution mask...'
            
        # Make a mask at the low resolution
        cmd = './makeSimpleImageMask ' + self._lowResMaskPath +' '+ self._lowResPathString
        MosaicUtilities.cmdRunner(cmd, self._lowResMaskPath, force)            
        self._lowResPathStringAndMask = self._lowResPathString +' '+ self._lowResMaskPath
        self._lowResMaskImageSize = IrgGeoFunctions.getImageSize(self._lowResMaskPath)
        
        # Compute the HRSC bounding box
        # - This is a pretty good estimate based on the metadata
        lowResNadirPath = self._lowResWarpedPaths[HRSC_NADIR]
        (minLon, maxLon, minLat, maxLat) = IrgGeoFunctions.getImageBoundingBox(lowResNadirPath)
        hrscBoundingBoxDegrees = MosaicUtilities.Rectangle(minLon, maxLon, minLat, maxLat)
        print 'Estimated HRSC bounds: ' + str(hrscBoundingBoxDegrees)
        
        # Cut out a region from the basemap around the location of the HRSC image
        # - We record the ROI in degrees and low res pixels
        print 'Generating low res base basemap region around HRSC data'
        CROP_BUFFER_LAT = 1.0
        CROP_BUFFER_LON = 1.0
        self._croppedRegionBoundingBoxDegrees = copy.copy(hrscBoundingBoxDegrees)
        self._croppedRegionBoundingBoxDegrees.expand(CROP_BUFFER_LON, CROP_BUFFER_LAT)
        self._croppedRegionBoundingBoxPixels = basemapInstance.degreeRoiToPixelRoi(self._croppedRegionBoundingBoxDegrees, False)
        basemapInstance.makeCroppedRegionDegrees(self._croppedRegionBoundingBoxDegrees, self._basemapCropPath)
        self._makeGrayscaleImage(self._basemapCropPath, self._basemapGrayCropPath)
        
        # Compute the spatial registration from the HRSC image to the base map
        self._computeBaseSpatialRegistration(basemapInstance, lowResNadirPath, force)
        
        # Compute the brightness scaling gains relative to the cropped base map
        # - This is done at low resolution
        # - The low resolution output is smoothed out later to avoid jagged edges.
        # TODO: Should we use the mask here?
        cmd = ('./computeBrightnessCorrection ' + self._basemapCropPath +' '+ self._lowResPathString +' '
                + self._lowResSpatialCroppedRegistrationPath +' '+ self._brightnessGainsPath)
        MosaicUtilities.cmdRunner(cmd, self._brightnessGainsPath, force)        
        
        print 'Finished with low resolution processing for HRSC set ' + setName
        
        # Now we have done everything we plan to with the low resolution maps
        
    def getSetName(self):
        return self._setName
    
    def getBoundingBoxDegrees(self):
        '''Returns the bounding box of the entire HRSC image'''
        return self._hrscBoundingBoxDegrees
        
    def _makeGrayscaleImage(self, inputPath, outputPath, force=False):
        '''Convert an image on disk to grayscale'''
        # Currently we just take the red channel, later we should experiment.
        cmd = 'gdal_translate -b 1 ' + inputPath +' '+ outputPath
        MosaicUtilities.cmdRunner(cmd, outputPath, force)
        
        
    def prepHighResolutionProducts(self, force=False):
        '''Generates all of the high resolution HRSC products'''
        
        # TODO: Accept an input Degree region and only generate the tiles surrounding that region.
        
        # Convert each high res input image into the output format
        
        # TODO: Could avoid this expensive step by handling the transform in the c++ programs
        # Generate a copy of each input HRSC channel at the high output resolution
        print 'Generating high resolution warped channel images...'
        self._generateHighResWarpedPaths(force)

        # Make a mask at the output resolution
        # - This mask is actually pretty small on disk since it compresses so well.
        print 'Generating high resolution mask...'
        cmd = './makeSimpleImageMask ' + self._highResMaskPath +' '+ self._highResPathString
        MosaicUtilities.cmdRunner(cmd, self._highResMaskPath, force)            
        self._highResPathStringAndMask = self._highResPathString +' '+ self._highResMaskPath        
        
        
        # Split the image up into tiles at the full output resolution       
        # - There is one list of tiles per HRSC channel
        # - Each channel gets its own subfolder
        # - Due to ImageMagick's implementation this step is already multithreaded!
        print 'Splitting warped images into tiles...'
        self._tileFolder = os.path.join(os.path.dirname(self._hrscBasePathOut), 'tiles') # TODO: Do we need no divide up the folders more?
        if not os.path.exists(self._tileFolder):
                os.mkdir(self._tileFolder)
        tileInfoLists = [[], [], [], [], []] # One list per channel
        for c in range(NUM_HRSC_CHANNELS):
            # Get the info for this channel
            warpedPath    = self._highResWarpedPaths[c]
            channelString = CHANNEL_STRINGS[c]
            
            # Write all the tiles to a folder and get the info list for those tiles
            channelOutputFolder = os.path.join(self._tileFolder, channelString)
            if not os.path.exists(channelOutputFolder):
                os.mkdir(channelOutputFolder)
            tileInfoLists[c] = splitImage(warpedPath, channelOutputFolder, HRSC_HIGH_RES_TILE_SIZE)
            
        # Verify that each channel generated the same number of tiles
        numTiles = len(tileInfoLists[0])
        for pathList in tileInfoLists:
            assert(len(pathList) == numTiles)
        print 'Generated ' +str(numTiles)+ ' tiles.'
        
        # TODO: We can probably delete the original warped images now
        
        # Loop through each of the tiles we created and consolidate information across channels
        print 'Consolidating tile information...'
        self._tileDict = {}
        for i in range(numTiles):
    
            # Check the percent valid to see if there is any content in this tile
            # - Percent valid is the only field that varies by channel
            # - All the other information can just be read from the first channel
            
            thisTileInfo = tileInfoLists[0][i] # Copy misc info from the first channel
            percentValid = 1.0
            for c in range(0,NUM_HRSC_CHANNELS):
                chanInfo = tileInfoLists[c][i]
                if chanInfo['percentValid'] < percentValid:
                    percentValid = chanInfo['percentValid']
            thisTileInfo['percentValid'] = percentValid
            if percentValid < 0.01:
                #print 'Dropping empty tile' + thisTileInfo['prefix']
                continue
    
            # Add in paths to the tile for each channel, including a joint string for convenience.
            allChannelsString = ''
            for c in range(NUM_HRSC_CHANNELS):
                pathKey = CHANNEL_STRINGS[c] + '_path'
                thisTileInfo[pathKey]  = tileInfoLists[c][i]['path'],
                allChannelsString     += tileInfoLists[c][i]['path'] + ' '
            thisTileInfo['allChannelsString'] = allChannelsString
            
            # Set up paths for the files we will generate for this tile
            filePrefix       = 'tile_' + thisTileInfo['prefix']
            tileInfoBasePath = os.path.join(self._tileFolder, filePrefix)
            thisTileInfo['colorPairPath'           ] = tileInfoBasePath+'_color_pairs.csv'
            thisTileInfo['colorTransformPath'      ] = tileInfoBasePath+'_color_transform.csv'
            thisTileInfo['newColorPath'            ] = tileInfoBasePath+'_new_color.tif'
            thisTileInfo['brightnessGainsPath'     ] = tileInfoBasePath+'_brightness_gains.csv'
            thisTileInfo['tileMaskPath'            ] = tileInfoBasePath+'_tile_mask.tif'
            thisTileInfo['allChannelsStringAndMask'] = allChannelsString + ' ' + thisTileInfo['tileMaskPath']
            #thisTileInfo['lonlatBounds'            ] = None # The data we have is incorrect at this point.
            thisTileInfo['spatialTransformToLowResBasePath'   ] = tileInfoBasePath+'_spatial_transform_to_low_res_base.csv'
            
            thisTileInfo['stillValid'] = True # Set this to false if there is an error processing this tile
            
            # Generate the transform from the high resolution tile to the low resolution basemap
            self._computeTileBoundsAndTransform(thisTileInfo, force)
               
            # Make a mask of valid pixels for this tile
            # - It would be great if the input images had a mask.
            # - Currently all-black pixels anywhere in the image get masked out!
            cmd = './makeSimpleImageMask ' + thisTileInfo['tileMaskPath'] +' '+ allChannelsString
            MosaicUtilities.cmdRunner(cmd, thisTileInfo['tileMaskPath'], force)
            
            key = thisTileInfo['prefix']
            self._tileDict[key] = thisTileInfo
    
        # Generate a "personalized" brightness file for each tile
        self._splitScaleBrightnessGains(self._brightnessGainsPath, self._tileDict, force)

        # TODO: Multithread these at some point, this is pretty fast though.
        # Generate the color transform for each tile
        # - HRSC colors are written with the brightness correction already applied
        # - Color pairs are computed between the high resolution HRSC tile and the low resolution input basemap.
        for tile in self._tileDict.itervalues():
            
            # TODO: In the future we may need another tile size for color transform computation!
            
            # Generate a set of color pairs
            cmd = ('./writeHrscColorPairs ' + self._basemapColorPath +' '+ tile['allChannelsStringAndMask']
                   +' '+ tile['spatialTransformToLowResBasePath'] +' '+ tile['brightnessGainsPath'] +' '+ tile['colorPairPath'])
            MosaicUtilities.cmdRunner(cmd, tile['colorPairPath'], force)        

            # Now compute the actual transform
            # TODO: Make this a function call!
            cmd = ('python /home/smcmich1/repo/MassUpload/solveHrscColor.py ' + tile['colorTransformPath']
                                                                              +' '+ tile['colorPairPath'])
            MosaicUtilities.cmdRunner(cmd, tile['colorTransformPath'], force)

        # Now that we have all the color transforms, generate the new color image for each tile.
        # - This function utilizes the thread pool
        self._generateNewHrscColorTiles(self._tileDict, force)
        
        #raise Exception('STOP TEST')
        
        print 'Finished generating high resolution content for HRSC image.'
        
        
        
    def _getWarpToProjectionCmd(self, sourcePath, outputFolder, postfix, metersPerPixel):
        '''Get the command needed by _warpToProjection'''
        if not os.path.exists(outputFolder):
            os.mkdir(outputFolder)
        fileName   = sourcePath[sourcePath.rfind('/')+1:]
        warpedPath = os.path.join(outputFolder, fileName)[:-4] + postfix +'.tif'
        cmd = ('gdalwarp ' + sourcePath +' '+ warpedPath + ' -r cubicspline '
                 +' -t_srs "'+self._basemapInstance.getProj4String()+'" -tr '
                 + str(metersPerPixel)+' '+str(metersPerPixel)+' -overwrite')
        return (cmd, warpedPath)
    
    def _warpToProjection(self, sourcePath, outputFolder, postfix, metersPerPixel, force=False):
        '''Warps an HRSC file into the specified projection space'''
        (cmd, warpedPath) = self._getWarpToProjectionCmd(sourcePath, outputFolder, postfix, metersPerPixel)
        MosaicUtilities.cmdRunner(cmd, warpedPath, force)
        return warpedPath
    
        
    def _generateHighResWarpedPaths(self, force):
        '''Generate all of the high resolution warped HRSC images using multiple threads'''
    
        if self._threadPool:
            print 'Launching multiple gdalwarp threads...'
            
            # For each path, make the command line call we want executed.
            cmdList = []
            self._highResWarpedPaths = []
            for path in self._inputHrscPaths:
                warpCmd, warpedPath = self._getWarpToProjectionCmd(path, self._outputFolder,
                                                                   '_output_res', self._basemapInstance.getHighResMpp())
                cmdList.append((warpCmd, warpedPath, force))
                self._highResWarpedPaths.append(warpedPath)
            # Pass all of these commands to a multiprocessing worker pool
            self._threadPool.map(MosaicUtilities.cmdRunnerWrapper, cmdList)
        
        else: # No pool, run single threaded.
            self._highResWarpedPaths = [self._warpToProjection(path, self._outputFolder, '_output_res', 
                                                      self._basemapInstance.getHighResMpp(), force)
                               for path in self._inputHrscPaths]        
        
        # Build up a string containing all the high res paths for convenience
        self._highResPathString = ''
        for path in self._highResWarpedPaths:
            self._highResPathString += path + ' '
        
        
    def getTileInfo(self, boundsDegrees=None, transformId=''):
        '''Return the high resolution tile information.
           If an ROI in degrees is passed in, only tiles that intersect that ROI will be returned and
           a transform to that ROI will be created under 'tileToTileTransformPath'.  Pass in the ID argument
           if you want the path to be unique!'''
        if boundsDegrees == None:
            return self._tileDict
        
        # Otherwise we need to make a new dictionary containing only the intersecting tiles
        outputDict = {}
        for key in self._tileDict:
            tile = copy.copy(self._tileDict[key])
            if boundsDegrees.overlaps(tile['degreeRect']):
                # Compute the transform to the provided ROI
                if transformId:
                    transformFileName = 'tile_to_tile_transform_' + tile['prefix'] + '_' + transformId+ '.csv'
                else: # Use a unique tile path
                    transformFileName = 'tile_to_tile_transform_' + tile['prefix'] + '.csv'
                tile['tileToTileTransformPath'] = os.path.join(self._tileFolder, transformFileName)
                self.getTransformToBasemapRoi(tile, boundsDegrees, tile['tileToTileTransformPath'])
                outputDict[key] = tile
        return outputDict
        
    


    def getTransformToBasemapRoi(self, tileInfo, basemapRoiDegrees, outputPath):
        '''Computes the transform of an HRSC tile to a specific output tile'''
        
        # Load the low res transform and compute to high res
        tf = MosaicUtilities.SpatialTransform(tileInfo['spatialTransformToLowResBasePath'])
        
        # Get the pixel ROI of the basemap tile
        basemapRoiHighResPixels = self._basemapInstance.degreeRoiToPixelRoi(basemapRoiDegrees, True)
        
        # Update the transform scale (low res -> high res)
        # - Also subtract out the pixel position of the tile
        scaling = self._basemapInstance.getLowResMpp() / self._basemapInstance.getHighResMpp()
        tf.setScaling(1.0) # The new transform is not converting pixel resolutions
        dx, dy = tf.getShift()
        tf.setShift(dx*scaling - basemapRoiHighResPixels.minX,
                    dy*scaling - basemapRoiHighResPixels.minY)
        
        # Write the output transform
        tf.write(outputPath)
        
        
    def _getHrscChannelPaths(self, hrscBasePath):
        '''Get the list of all HRSC channel paths from the base path'''
        # This skips the channels we are not interested in
        return (hrscBasePath+'_re3.tif',
                hrscBasePath+'_gr3.tif',
                hrscBasePath+'_bl3.tif',
                hrscBasePath+'_ir3.tif',
                hrscBasePath+'_nd3.tif')
    
    
    def _estimateRegistration(self, baseImage, otherImage, outputPath):
        '''Writes an estimated registration transform to a file based on geo metadata.'''
        # This function assumes the images are in the same projection system!
        
        # Get the projection bounds and size in both images
        baseGeoInfo     = IrgGeoFunctions.getImageGeoInfo(baseImage,  False)
        otherGeoInfo    = IrgGeoFunctions.getImageGeoInfo(otherImage, False)
        baseProjBounds  = baseGeoInfo[ 'projection_bounds']
        otherProjBounds = otherGeoInfo['projection_bounds']
        baseImageSize   = baseGeoInfo[ 'image_size']
        otherImageSize  = otherGeoInfo['image_size']
            
        # Now estimate the bounding box of the other image in the base image
        topLeftCoord = projCoordToPixelCoord(otherProjBounds[0], otherProjBounds[3], baseGeoInfo)
        
        transform = MosaicUtilities.SpatialTransform()
        transform.setShift(topLeftCoord[0], topLeftCoord[1])
        transform.write(outputPath)
    
        return topLeftCoord    
    
    def _transformToRect(self, transform, isHighRes=False):
        '''Converts an HRSC transform into an HRSC ROI at high or low resolution'''
        tx, ty = transform.getShift()
        if isHighRes:
            rect = MosaicUtilities.Rectangle(tx, tx+self._highResMaskImageSize[0], ty, ty+self._highResMaskImageSize[1])
        else:
            rect = MosaicUtilities.Rectangle(tx, tx+self._lowResMaskImageSize[0], ty, ty+self._lowResMaskImageSize[1])
        return rect
    
    def _rectToTransform(self, rectangle, outputPath):
        '''Convert a pixel ROI into a transform and write it to disk'''
        tf = MosaicUtilities.SpatialTransform()
        tf.setShift(rectangle.minX, rectangle.minY)
        tf.write(outputPath)
    
    def _computeBaseSpatialRegistration(self, basemapInstance, hrscPath, force=False):
        '''Compute the spatial registration from the HRSC image to the base image'''
    
        ## Estimate the spatial transform using the image metadata
        ## - This is against the full basemap image
        #estimatedTransformPath = self._hrscBasePathOut + 'spatial_transform_basemap_estimated.csv'
        #estX, estY = self._estimateRegistration(self._basemapColorPath, hrscPath, estimatedTransformPath)
        #
        ## Update the estimated transform to apply to the cropped region
        #estimatedCroppedTransformPath = self._hrscBasePathOut + 'spatial_transform_cropped_estimated.csv'
        #basemapInstance.updateTransformToBoundsDegrees(estimatedTransformPath, estimatedCroppedTransformPath,
        #                                               self._croppedRegionBoundingBoxDegrees, False)

        # Estimate the spatial transform using the image metadata
        # - This is against the cropped basemap image
        estimatedCroppedTransformPath = self._hrscBasePathOut + '_spatial_transform_cropped_estimated.csv'
        estX, estY = self._estimateRegistration(self._basemapGrayCropPath, hrscPath, estimatedCroppedTransformPath)

        # TODO: Check the number of inliers!    
        # Refine the spatial transform using image data
        # - This is computed to the low resolution cropped image
        cmd = ('./RegisterHrsc ' + self._basemapGrayCropPath +' '+ hrscPath
               +' '+ self._lowResSpatialCroppedRegistrationPath +' '+ str(1.0) +' '+ estimatedCroppedTransformPath)
        MosaicUtilities.cmdRunner(cmd, self._lowResSpatialCroppedRegistrationPath, force)

        # Load the transform we just computed and convert it to a bounding box
        lowResCroppedPixelRoi  = self._transformToRect(MosaicUtilities.SpatialTransform(self._lowResSpatialCroppedRegistrationPath), False)
        # Convert so that the ROI is relative to the entire basemap
        lowResPixelRoi = copy.copy(lowResCroppedPixelRoi)
        lowResPixelRoi.shift(self._croppedRegionBoundingBoxPixels.minX, self._croppedRegionBoundingBoxPixels.minY)
        
        # Convert so we have a high resolution basemap pixel ROI
        highResPixelRoi = basemapInstance.convertPixelRoiResolution(lowResPixelRoi, False)
        
        # Convert back to a transform and record to disk
        self._rectToTransform(highResPixelRoi, self._highResSpatialRegistrationPath)
        
        # Also convert to a bounding box in degrees
        self._hrscBoundingBoxDegrees = basemapInstance.pixelRoiToDegreeRoi(highResPixelRoi, True)

    
    def _computeTileBoundsAndTransform(self, tileInfo, force=False):
        '''Compute boundary and transform information for this high resolution tile'''

        # Compute a transform going from a high resolution HRSC tile to the low resolution basemap
        
        # Load the transform from high res HRSC to high res basemap
        tf = MosaicUtilities.SpatialTransform(self._highResSpatialRegistrationPath)
        dx, dy = tf.getShift()
        
        # Record the high resolution pixel ROI
        minHighResCol = dx + tileInfo['pixelCol']
        minHighResRow = dy + tileInfo['pixelRow']
        highResPixelRect = MosaicUtilities.Rectangle(minHighResCol, minHighResCol + tileInfo['widthPixels' ],
                                                     minHighResRow, minHighResRow + tileInfo['heightPixels'])
        tileInfo['highResPixelRect'] = highResPixelRect
        
        # Record the ROI in degrees
        degreeRect = self._basemapInstance.pixelRoiToDegreeRoi(highResPixelRect, True)
        tileInfo['degreeRect'] = degreeRect
        
        # Update the transform scale (high res -> low res)
        scaling = self._basemapInstance.getHighResMpp() / self._basemapInstance.getLowResMpp()
        tf.setScaling(scaling)

        # Factor in the tile shift (converting to low res pixels)
        tf.setShift(minHighResCol*scaling,
                    minHighResRow*scaling)
        
        # Write out the new transform
        tf.write(tileInfo['spatialTransformToLowResBasePath'])
   

    
    
    def _splitScaleBrightnessGains(self, fullPath, tileDict, force=False):
        '''Generates a brightness gains file for a single tile'''
    
        print 'Generating split brightness gains...'
        
        scaling = self._basemapInstance.getHighResMpp() / self._basemapInstance.getLowResMpp()
        #print scaling
        
        # Read in the entire input file
        lowResVals = numpy.loadtxt(fullPath, skiprows=1, delimiter=',', usecols=(0,))   
        lowResRows = range(0,len(lowResVals))
        
        for tile in tileDict.itervalues():
            
            outputPath = tile['brightnessGainsPath']
            if not force and os.path.exists(outputPath):
                continue
            
            # Compute the row range in the input tile
            tileHeight       = tile['heightPixels']
            pixelRow         = tile['pixelRow'    ]
            fullSizeStartRow = pixelRow
            fullSizeStopRow  = (pixelRow+tileHeight)
            
            #print fullSizeStartRow
            #print fullSizeStopRow
            
            # For each desired ouput value (high res), compute the location in the input values (low res)
            thisTileRowsInInput = numpy.empty([tileHeight])
            index = 0
            for r in range(fullSizeStartRow, fullSizeStopRow):
                thisTileRowsInInput[index] = r * scaling
                index += 1
            # Use numpy to interpolate values
            #print thisTileRowsInInput
            
            thisTileVals = numpy.interp(thisTileRowsInInput, lowResRows, lowResVals)
            zeroCol      = [0 for i in thisTileVals]
                
            # Write out the interpolated values
            numpy.savetxt(outputPath, thisTileVals, header=str(tileHeight),  fmt='%1.6f, 0.0', comments='')  


    def _getAdjacentTiles(self, tile, tileDict):
        '''Gets a list containing all the (still valid) tiles which are adjacent to the provided tile'''
        
        tileRow = tile['tileRow'] # The location of the input tile
        tileCol = tile['tileCol']
        
        # Build a list of the adjacent tiles
        adjacentTileList = []
        for r in range(-1,2):
            for c in range(-1,2):
                if (r==0) and (c==0): # Skip the main tile
                    continue
                try:
                    prefix  = getTilePrefix(tileRow+r, tileCol+c)
                    adjTile = tileDict[prefix]
                    if adjTile['stillValid']:
                        adjTile['rowOffset'] = r
                        adjTile['colOffset'] = c
                        adjacentTileList.append(adjTile)
                except:
                    pass # This means this tile does not actually exist
        
        return adjacentTileList    
    
    
    def _generateNewHrscColorTiles(self, tileDict, force=False):
        '''Generate a new color image for each HRSC tile'''

        # Make one pass through all the tiles just to generate the required commands
        tileCommandList = []
        for tile in self._tileDict.itervalues():
            
            if not tile['stillValid']: # Skip tiles which have already failed
                return False

            #try:
            # Get a list af adjacent tiles to pass in to the color transformer
            adjacentTiles = self._getAdjacentTiles(tile, tileDict)
            
            # Compute a weighting for each tile based on the pixel count
            totalWeight = 1.0 # The main image is the reference so it has weight 1 initially
            for adjTile in adjacentTiles:
                totalWeight += (adjTile['percentValid'] / tile['percentValid'])
                
            # Generate the parameter sequence for the next program call
            adjacentTileString = ''
            for adjTile in adjacentTiles:
                tileWeight     = (adjTile['percentValid'] / tile['percentValid']) / totalWeight
                thisTileString = adjTile['colorTransformPath'] +' '+ str(tileWeight) +' '+ str(adjTile['colOffset']) +' '+ str(adjTile['rowOffset'])
                adjacentTileString += (thisTileString + ' ')
            mainWeight = 1.0/totalWeight # Normalize the main weight too
            
            # Transform the HRSC image color
            # - Brightness correction is applied before color transform
            cmd = ('./transformHrscImageColor ' + tile['allChannelsStringAndMask'] +' '+ tile['brightnessGainsPath'] +' '+ tile['newColorPath'] +' '+
                                                  tile['colorTransformPath'] +' '+ str(mainWeight) +' '+ adjacentTileString
                                                  )
            tileCommandList.append((cmd, tile['newColorPath'], force))
            #except CmdRunException:
            #    tile['stillValid'] = False
        
        # Make a second pass to execute the commands
        # - Doing this in two passes lets us easily utilize a thread pool.
        
        if self._threadPool: # Dispatch the commands to the worker pool
            self._threadPool.map(MosaicUtilities.cmdRunnerWrapper, tileCommandList)
        else: # Run the commands one after the other
            for command in tileCommandList:
                MosaicUtilities.cmdRunnerWrapper(command)