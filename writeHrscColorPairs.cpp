

#include <stdio.h>
#include <opencv2/opencv.hpp>

#include <vw/Math/Geometry.h>
#include <vw/Math/RANSAC.h>
#include <vw/InterestPoint/InterestData.h>

#include <HrscCommon.h>


//=============================================================


bool loadInputImages(int argc, char** argv, cv::Mat &basemapImage, std::vector<cv::Mat> &hrscChannels, 
                     cv::Mat &transform, std::string &outputPath)
{
  std::vector<std::string> hrscPaths(NUM_HRSC_CHANNELS);
  std::string baseImagePath = argv[1];
  hrscPaths[0] = argv[2];
  hrscPaths[1] = argv[3];
  hrscPaths[2] = argv[4];
  hrscPaths[3] = argv[5];
  hrscPaths[4] = argv[6];
  std::string spatialTransformPath = argv[7];
  outputPath  = argv[8];
  

  const int LOAD_GRAY = 0;
  const int LOAD_RGB  = 1;
  
  // Load the base map
  cv::Mat basemapImage = cv::imread(baseImagePath, LOAD_RGB);
  if (!basemapImage.data)
  {
    printf("Failed to load base map image!\n");
    return false;
  }

  // Load all of the HRSC images
  for (size_t i=0; i<NUM_HRSC_CHANNELS; ++i)
  {
    hrscChannels[i] = cv::imread(hrscPaths[i], LOAD_GRAY);
    if (!hrscChannels[i].data)
    {
      printf("Failed to load HRSC image: %s\n", hrscPaths[i].c_str());
      return false;
    }
  }

  // Load the spatial transform
  if (!readTransform(spatialTransformPath, transform))
    return false;

  return true;
}


bool writeColorPairs(const cv::Mat &basemapImage, const cv::Mat &spatialTransform, const std::vector<cv::Mat> hrscChannels,
                     const std::string outputPath)
{
  // Control what percentage of the pixel pairs we use
  const int SAMPLE_DIST = 10;
 

  std::ofstream outputFile(outputPath,c_str());

  // Iterate over the pixels of the HRSC image
  bool gotValue;
  std::vector<unsigned char> baseValues(NUM_BASE_CHANNELS);
  for (int r=0; r<hrscChannels[0].rows; r+=SAMPLE_DIST)
  {
    for (int c=0; c<hrscChannels[0].cols; c+=SAMPLE_DIST)
    {     
      // Compute the equivalent location in the basemap image
      float baseX = c*transform.at<float>(0,0) + r*transform.at<float>(0,1) + transform.at<float>(0,2);
      float baseY = c*transform.at<float>(1,0) + r*transform.at<float>(1,1) + transform.at<float>(1,2);
      
      // Extract all of the basewap values at that location
      for (size_t i=0; i<NUM_BASE_CHANNELS; ++i)
        baseValues[i] = interpPixel<unsigned char>(basemapImage, basemapImage, baseX, baseY, gotValue);
           
      if (gotValue) // Write all the values to a line in the file
      {
        for (size_t i=0; i<NUM_BASE_CHANNELS; ++i)
          outputFile << baseValues[i] <<", ";
        for (size_t i=0; i<NUM_HRSC_CHANNELS-1; ++i)
          outputFile << hrscChannel[i].get<unsigned char>(r,c) <<", "<<
        outputFile << hrscChannel[NUM_HRSC_CHANNELS-1].get<unsigned char>(r,c) << std::endl;
      }
    } // End col loop
  } // End row loop
  
  outputFile.close();

  return true;
}

//============================================================================


int main(int argc, char** argv)
{
  // Check input arguments
  if (argc < 9)
  {
    printf("usage: WriteColorPairs <Base Image Path> <HRSC Red> <HRSC Green> <HRSC Blue> <HRSC NIR> <HRSC Nadir> <Transform File Path> <Output Path>\n");
    return -1;
  }
  
  // Load the input images  
  cv::Mat basemapImage, spatialTransform, outputPath;
  std::vector<cv::Mat> hrscChannels(NUM_HRSC_CHANNELS);
  if (!loadInputImages(argc, argv, basemapImage, hrscChannels, spatialTransform, outputPath))
    return -1;

  // TODO: The spatial transform should be from HRSC to BASEMAP

  // Generate the list of color pairs
  writeColorPairs(basemapImage, spatialTransform, hrscChannels, outputPath);

  return 0;
}

