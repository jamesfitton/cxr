#!/usr/bin/env python
# coding: utf-8
# In[ ]:
def cloudMask(image):
	""" Masks clouds within the image based on QA bands
	:param image: an image
	:type: image: ee.Image
	:return: Input image masked to cloud free areas
	:rtype: ee.Image
	"""
	#select the QA band and the B4 band
	qa = image.select('QA60').int16()
	qab4 = image.select("B4")

	cloudBitMask = 1024 #qa.bitwiseAnd(Math.pow(2, 10)) or 1 << 10
	cirrusBitMask = 2048 #aq.bitwiseAnd(Math.pow(2, 11)) or 1 << 11

	#Both flags should be set to zero, indicating clear conditions. //b1 1500 add?
	mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0)).And(qab4.lt(2500))

	return image.updateMask(mask)

def gridCellCollectionClip(image):
	return image.clip(gridCell).select(['B2', 'B3', 'B4', 'B8', 'B11']).rename('blue', 'green', 'red', 'nir', 'swir1')
	
def removeDuplicates(image):
	""" Adds an ID to the image properties based on date and tile.
	:param image: an image
	:type: image: ee.Image
	:return: Input image with dateID property
	:rtype: ee.Image
	"""
	#get the image date
	date = ee.String(image.get('system:index')).slice(0,8)
	#get the image tile
	tile = ee.String(image.get('MGRS_TILE'))
	#create the id        
	dateId = date.cat('_').cat(tile)

	return ee.Image(image).set('dateId', dateId)

def replace(image, to_replace, to_add):
	""" Replace one band of the image with a provided band from https://github.com/gee-community/gee_tools
	:param to_replace: name of the band to replace. If the image hasn't got
		that band, it will be added to the image.
	:type to_replace: str
	:param to_add: Image (one band) containing the band to add. If an Image
		with more than one band is provided, it uses the first band.
	:type to_add: ee.Image
	:return: Same Image provided with the band replaced
	:rtype: ee.Image
	"""
	band = to_add.select([0])
	bands = image.bandNames()
	resto = bands.remove(to_replace)
	img_resto = image.select(resto)
	img_final = img_resto.addBands(band)
	return img_final

def mosaicSameDay(collection):
	""" Return a collection where images from the same day are mosaicked from https://github.com/gee-community/gee_tools
	:param reducer: the reducer to use for merging images from the same day.
		Defaults to 'first'
	:type reducer: ee.Reducer
	:return: a new image collection with 1 image per day. The only property
		kept is `system:time_start`
	:rtype: ee.ImageCollection
	"""
	def make_date_list(img, l):
		l = ee.List(l)
		img = ee.Image(img)
		date = img.date()
		# make clean date
		day = date.get('day')
		month = date.get('month')
		year = date.get('year')
		clean_date = ee.Date.fromYMD(year, month, day)
		condition = l.contains(clean_date)

		return ee.Algorithms.If(condition, l, l.add(clean_date))

	col_list = collection.toList(collection.size())
	date_list = ee.List(col_list.iterate(make_date_list, ee.List([])))

	first_img = ee.Image(collection.first())
	bands = first_img.bandNames()

	def make_col(date):
		date = ee.Date(date)
		filtered = collection.filterDate(date, date.advance(1, 'day'))

		#mean azimuth of images
		meanAzimuth = filtered.aggregate_array('MEAN_SOLAR_AZIMUTH_ANGLE')
		meanAzimuth = ee.List(meanAzimuth).reduce(ee.Reducer.mean())

		#mean zenith 
		meanZenith = filtered.aggregate_array('MEAN_SOLAR_ZENITH_ANGLE')
		meanZenith = ee.List(meanZenith).reduce(ee.Reducer.mean())

		#mean cloud cover
		meanCloud = filtered.aggregate_array('CLOUD_COVERAGE_ASSESSMENT')
		meanCloud = ee.List(meanCloud).reduce(ee.Reducer.mean()) 

		#number of images
		numberImages = filtered.size()

		mosaic = filtered.mosaic()
		mosaic = mosaic.set('system:time_start', date.millis(),
							'system:footprint', mergeGeometries(filtered))

		mosaic = mosaic.rename(bands)
		def reproject(bname, mos):
			mos = ee.Image(mos)
			mos_bnames = mos.bandNames()
			bname = ee.String(bname)
			proj = first_img.select(bname).projection()

			newmos = ee.Image(ee.Algorithms.If(
				mos_bnames.contains(bname),
				replace(mos, bname, mos.select(bname).setDefaultProjection(proj)),
				mos))

			return newmos

		mosaic = ee.Image(bands.iterate(reproject, mosaic))
		return mosaic        .set('dateTime', filtered.first().get('system:time_start'))        .set('MEAN_SOLAR_ZENITH_ANGLE', meanZenith)        .set('MEAN_SOLAR_AZIMUTH_ANGLE', meanAzimuth)        .set('CLOUD_COVERAGE_ASSESSMENT', meanCloud)        .set('mosaicImageCount', numberImages)

	new_col = ee.ImageCollection.fromImages(date_list.map(make_col))
	return new_col

def setImageArea(image):
	""" Calculates the area of the image covered by the image
	:param image: an image
	:type: image: ee.Image
	:return: Input image with gridCellCoverage property added
	:rtype: ee.Image
	"""
	#select a 10m band from the image
	blueBand = image.select('blue')
#         imageArea = blueBand.multiply(ee.Image.pixelArea())
	imageArea = blueBand.reduceRegion(**{
		'reducer': ee.Reducer.count(),
		'scale': 10,
		'geometry': gridCell})\
		.get('blue')

	imageArea = ee.Number(imageArea).multiply(100) #100 m2 cell area (10*10)


	gridCellArea = gridCell.area(1)

	return image.set('gridCellCoverage', ee.Number(imageArea).divide(gridCellArea).multiply(100))#.set('gridCellArea', gridCellArea).set('imageArea', imageArea)

#FUNCTION: identifies the cloud cover threshold to use in order to return a minimum number of images
def setFilterDecending(collection, startFilter, interval, iterations, minImages, defaultFilter): 
	""" Identifies the cloud cover threshold to use in order to return a minimum number of images
	:param collection: an image collection
	:type: collection: ee.ImageCollection 
	:param startFilter: the intitial cloud cover filter value
	:type: startFilter: ee.Float  
	:param interval: the steps/interval that the filter should be reduced by
	:type: interval:  ee.Float 
	:param iterations: the number of filters to test
	:type: iterations:  ee.Int   
	:param minImages: the minimum number of images required to produce the water occurrence output
	:type: minImages:  ee.Int  
	:param defaultFilter: the default filter to use if no suitable filter is identified
	:type: defaultFilter:  ee.Float   
	:return: Cloud cover filter value used to filter out images with inadequate cell coverage
	:rtype: ee.Float
	"""
	#creates a list to map through and craetes the filter values to test
	list = ee.List.sequence(0, iterations, ee.Number(interval).toFloat()) 

	def minImageTest(i):
		#creates the test filter value
		listIndex = list.indexOf(i)
		#decending filter value, hence subtract
		filterValue = ee.Number(startFilter).subtract(listIndex.multiply(interval)) 

		#filters the collection using the test value
		collectionFiltered = collection.filterMetadata('gridCellCoverage', 'greater_than', filterValue)

		#tests whether the collection is larger than the minimum number of images
		minImagesTest = ee.Algorithms.IsEqual(collectionFiltered.size().gte(minImages), 0)

		#if the filter value produces the minimum number if images the filter value is returned
		#condition, trueCase, falseCase)
		return ee.Algorithms.If(minImagesTest, None, filterValue) 

	bestFilterList = list.map(minImageTest)

	#bestFilterList is a list of the filter values that meet the minimum number of images value

	#removes nulls from the list and sorts it in ascending order
	bestFilter = bestFilterList.removeAll([None]).sort().reverse() #need to reverse the list if using decending filter values

	#test whether the list is empty
	allNulls = ee.Algorithms.IsEqual(bestFilter.size(), 0)

	#condition, trueCase, falseCase
	return ee.Algorithms.If(allNulls, defaultFilter, bestFilter.get(0))

def NDWI(image):
	""" Calculates the NDWI of an image
	:param image: an image
	:type: image: ee.Image
	:return: An image with a single ndwi band
	:rtype: ee.Image
	"""
	return image.normalizedDifference(['green', 'nir']).select(['nd'],['ndwi'])

def ndwiWater(image):
	""" Identifies water in an image based on the ndwi and a threshold value
	:param image: an image
	:type: image: ee.Image
	:return: An image where water is identified as 1 and non-water as 0
	:rtype: ee.Image
	"""
	# find the water using a fixed NDWI threshold
	NDWIWater = image.gt(ndwiThreshold)

	#clean up the image #look into dilation in morphology module
	connectedPixelSize = 512 #this can cause an internal error if set too high (1024 created errors for some cells)
	def waterClean(image, size):
		connectedPixels = image.int().connectedPixelCount(size)
		pixelCountWhere = image.where(connectedPixels.lt(size), 0)
		return pixelCountWhere

	imageCleaned = waterClean(NDWIWater, connectedPixelSize)

	time = image.get('system:time_start')

	return imageCleaned.set('system:time_start', time)

def featureProperties(date):
	""" Allocates image properties to the grid cell feature properties
	:param date: image date
	:type: date: ee.Date
	:return: A feature of the grid cell with a range of properties
	:rtype: ee.Feature
	"""
	image = gridCellCollection.filterMetadata('dateTime', 'equals', date).first()
	imageId = image.get('system:index')
	meanAzimuth = image.get('MEAN_SOLAR_AZIMUTH_ANGLE')
	meanZenith = image.get('MEAN_SOLAR_ZENITH_ANGLE')
	cloudCoverage = image.get('CLOUD_COVERAGE_ASSESSMENT')
	mosaicImageCount = image.get('mosaicImageCount')


	return gridCellFeature.set('date', ee.Date(date).format("YYYY-MM-dd HH:mm")).set('dateMilli', date).set('imageID', imageId).set('imageMeanAzimuth', meanAzimuth).set('imageMeanZenith', meanZenith).set('imageCloudCover', cloudCoverage).set('numberOfImagesAnalysed', ee.Number(gridCellNDWICollectionSize)).set('analysisStart', ee.Date(startDate).format("YYYY-MM-dd")).set('analysisEnd', ee.Date(endDate).format("YYYY-MM-dd")).set('earliestImageAnalysed', earliestImage).set('latestImageAnalysed', latestImage).set('cellCloudCoverThreshold', ee.Number(cellCloudCoverFilterValue)).set('cell', ee.Number(cellNumber)).set('cellResolution', ee.Number(gridResolution)).set('mosaicImageCount', mosaicImageCount)
	
def mergeGeometries(collection):
	""" Merge the geometries of many images. Return ee.Geometry """
	imlist = collection.toList(collection.size())

	first = ee.Image(imlist.get(0))
	rest = imlist.slice(1)

	def wrap(img, ini):
		ini = ee.Geometry(ini)
		img = ee.Image(img)
		geom = img.geometry()
		union = geom.union(ini)
		return union.dissolve()

	return ee.Geometry(rest.iterate(wrap, first.geometry()))
