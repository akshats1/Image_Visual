import slideio

# Path to the input CZI file
slide_wsi = "/media/akshat/New Volume/Image_Visualization/deepzoom/Image_3.czi"

# Open the slide as a CZI file
slide_czi = slideio.open_slide(slide_wsi, "CZI")

# Get the number of scenes in the CZI file
num_scenes = slide_czi.num_scenes

# Define default parameters for the conversion (specific to SVS format)
params = slideio.SVSJp2KParameters()

# Path to the output directory where the SVS files will be saved
output_dir = "/home/akshat/output_svs"

# Convert each scene to an SVS file and save it to the output directory
for j in range(num_scenes):
    # Get the scene object
    scene = slide_czi.get_scene(j)
    
    # Define the output path for the SVS file
    output_path = f"{output_dir}/scene_{j+1}.svs"
    
    # Convert the scene to SVS format and save it to the output path
    slideio.convert_scene(scene, params, output_path)

    # Print a message indicating the scene has been converted
    print(f"Scene {j+1} has been converted and saved to {output_path}")

