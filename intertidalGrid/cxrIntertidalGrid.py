def focalMax(image, radius):
    """Smooths a raster using focal max

    Args:
        image (image): the image to be smoothed
        radius (int): the radius of the smoothing kernal

    Returns:
        Smoothed image
    """
    dilation = image.fastDistanceTransform().sqrt().lte(radius)
    return dilation

def clip(i):
    """Clips an image to a geometry
    
    Args:
        i (geometry): the geometry to clip the image to
    
    Returns:
        Clipped image    
    """
    return i.clip(aoiGrid)

def stringToNumber(feature):
    """Converts the cell number string to a number

    Args:
        feature (feature): the grid cell feature with cell property

    Returns:
        A feature with cell property as a number
    """
    cell = ee.Number.parse(feature.get('cell')).int64()
    return feature.set('cell', cell).select(['cell'])


