#!/usr/bin/env python
#
# deepzoom_multiserver - Example web application for viewing multiple slides
#
# Copyright (c) 2010-2015 Carnegie Mellon University
# Copyright (c) 2021-2023 Benjamin Gilbert
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of version 2.1 of the GNU Lesser General Public License
# as published by the Free Software Foundation.
#
# This library is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public
# License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this library; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#

from argparse import ArgumentParser
import base64
from collections import OrderedDict
from io import BytesIO
import os
from threading import Lock
import zlib

from PIL import ImageCms, Image
from flask import Flask, abort, make_response, render_template, url_for


import PIL.Image

import PIL.Image


PIL.Image.MAX_IMAGE_PIXELS = None

from argparse import ArgumentParser
import base64
from collections import OrderedDict
from io import BytesIO
import os
from threading import Lock
import zlib

from PIL import Image, ImageCms
from flask import Flask, abort, make_response, render_template, url_for

import openslide
from openslide import OpenSlide, OpenSlideCache, OpenSlideError, OpenSlideVersionError
from openslide.deepzoom import DeepZoomGenerator

import logging
from logging.handlers import RotatingFileHandler

# Optimized sRGB v2 profile, CC0-1.0 license
# https://github.com/saucecontrol/Compact-ICC-Profiles/blob/bdd84663/profiles/sRGB-v2-micro.icc
SRGB_PROFILE_BYTES = zlib.decompress(
    base64.b64decode(
        'eNpjYGA8kZOcW8wkwMCQm1dSFOTupBARGaXA/oiBmUGEgZOBj0E2Mbm4wDfYLYQBCIoT'
        'y4uTS4pyGFDAt2sMjCD6sm5GYl7K3IkMtg4NG2wdSnQa5y1V6mPADzhTUouTgfQHII5P'
        'LigqYWBg5AGyecpLCkBsCSBbpAjoKCBbB8ROh7AdQOwkCDsErCYkyBnIzgCyE9KR2ElI'
        'bKhdIMBaCvQsskNKUitKQLSzswEDKAwgop9DwH5jFDuJEMtfwMBg8YmBgbkfIZY0jYFh'
        'eycDg8QthJgKUB1/KwPDtiPJpUVlUGu0gLiG4QfjHKZS5maWk2x+HEJcEjxJfF8Ez4t8'
        'k8iS0VNwVlmjmaVXZ/zacrP9NbdwX7OQshjxFNmcttKwut4OnUlmc1Yv79l0e9/MU8ev'
        'pz4p//jz/38AR4Nk5Q=='
    )
)
SRGB_PROFILE = ImageCms.getOpenProfile(BytesIO(SRGB_PROFILE_BYTES))


def create_app(config=None, config_file=None):
    # Create and configure app
    app = Flask(__name__)
    app.config.from_mapping(
        SLIDE_DIR='.',
        SLIDE_CACHE_SIZE=10,
        SLIDE_TILE_CACHE_MB=128,
        DEEPZOOM_FORMAT='jpeg',
        DEEPZOOM_TILE_SIZE=254,
        DEEPZOOM_OVERLAP=1,
        DEEPZOOM_LIMIT_BOUNDS=True,
        DEEPZOOM_TILE_QUALITY=75,
        DEEPZOOM_COLOR_MODE='default',
    )
    app.config.from_envvar('DEEPZOOM_MULTISERVER_SETTINGS', silent=True)
    if config_file is not None:
        app.config.from_pyfile(config_file)
    if config is not None:
        app.config.from_mapping(config)

    # Enable Flask debug mode
    app.debug = True

    # Set up logging
    handler = RotatingFileHandler('server.log', maxBytes=10000, backupCount=1)
    handler.setLevel(logging.DEBUG)
    app.logger.addHandler(handler)

    # Set up cache
    app.basedir = os.path.abspath(app.config['SLIDE_DIR'])
    config_map = {
        'DEEPZOOM_TILE_SIZE': 'tile_size',
        'DEEPZOOM_OVERLAP': 'overlap',
        'DEEPZOOM_LIMIT_BOUNDS': 'limit_bounds',
    }
    opts = {v: app.config[k] for k, v in config_map.items()}
    app.cache = _SlideCache(
        app.config['SLIDE_CACHE_SIZE'],
        app.config['SLIDE_TILE_CACHE_MB'],
        opts,
        app.config['DEEPZOOM_COLOR_MODE'],
    )

    # Helper functions
    def get_slide(path):
        path = os.path.abspath(os.path.join(app.basedir, path))
        app.logger.debug(f'Trying to open slide at path: {path}')
        if not path.startswith(app.basedir + os.path.sep):
            # Directory traversal
            abort(404)
        if not os.path.exists(path):
            abort(404)
        try:
            slide = app.cache.get(path)
            slide.filename = os.path.basename(path)
            return slide
        except OpenSlideError as e:
            app.logger.error(f'OpenSlideError: {e}')
            if path.lower().endswith(('.jpeg', '.jpg', '.png', '.tiff', '.tif')):
                return Image.open(path)
            abort(404)

    # Set up routes
    @app.route('/')
    def index():
        return render_template('files.html', root_dir=_Directory(app.basedir))

    @app.route('/<path:path>')
    def slide(path):
        slide = get_slide(path)
        slide_url = url_for('dzi', path=path)
        return render_template(
            'slide-fullpage.html',
            slide_url=slide_url,
            slide_filename=slide.filename,
            slide_mpp=getattr(slide, 'mpp', 0),
        )

    @app.route('/<path:path>.dzi')
    def dzi(path):
        slide = get_slide(path)
        format = app.config['DEEPZOOM_FORMAT']
        if isinstance(slide, Image.Image):
            return abort(404)  # Non-DeepZoom image, cannot generate DZI
        resp = make_response(slide.get_dzi(format))
        resp.mimetype = 'application/xml'
        return resp

    @app.route('/<path:path>_files/<int:level>/<int:col>_<int:row>.<format>')
    def tile(path, level, col, row, format):
        slide = get_slide(path)
        format = format.lower()
        if format not in ['jpeg', 'png']:
            # Not supported by Deep Zoom
            abort(404)
        try:
            if hasattr(slide, 'get_tile'):
                tile = slide.get_tile(level, (col, row))
            else:
                tile = slide
        except ValueError as e:
            # Invalid level or coordinates
            app.logger.error(f'ValueError: {e}')
            abort(404)
        app.cache.transform(tile)
        buf = BytesIO()
        tile.save(
            buf,
            format,
            quality=app.config['DEEPZOOM_TILE_QUALITY'],
            icc_profile=tile.info.get('icc_profile'),
        )
        resp = make_response(buf.getvalue())
        resp.mimetype = 'image/%s' % format
        return resp

    return app


class _SlideCache:
    def __init__(self, cache_size, tile_cache_mb, dz_opts, color_mode):
        self.cache_size = cache_size
        self.dz_opts = dz_opts
        self.color_mode = color_mode
        self._lock = Lock()
        self._cache = OrderedDict()
        # Share a single tile cache among all slide handles, if supported
        try:
            self._tile_cache = OpenSlideCache(tile_cache_mb * 1024 * 1024)
        except OpenSlideVersionError:
            self._tile_cache = None

    def get(self, path):
        with self._lock:
            if path in self._cache:
                # Move to end of LRU
                slide = self._cache.pop(path)
                self._cache[path] = slide
                return slide

        if path.lower().endswith(('.jpeg', '.jpg', '.png', '.tiff', '.tif')):
            slide = Image.open(path)
            slide.mpp = 0  # No mpp for non-OpenSlide images
            slide.transform = lambda img: None
        else:
            osr = OpenSlide(path)
            if self._tile_cache is not None:
                osr.set_cache(self._tile_cache)
            slide = DeepZoomGenerator(osr, **self.dz_opts)
            try:
                mpp_x = osr.properties[openslide.PROPERTY_NAME_MPP_X]
                mpp_y = osr.properties[openslide.PROPERTY_NAME_MPP_Y]
                slide.mpp = (float(mpp_x) + float(mpp_y)) / 2
            except (KeyError, ValueError):
                slide.mpp = 0
            slide.transform = self._get_transform(osr)

        with self._lock:
            if path not in self._cache:
                if len(self._cache) == self.cache_size:
                    self._cache.popitem(last=False)
                self._cache[path] = slide
        return slide

    def _get_transform(self, osr):
        if self.color_mode == 'default':
            return lambda img: None

        icc_profile = osr.properties.get('openslide.comment')
        if icc_profile:
            icc_profile = icc_profile[1:][:-1]
            icc_profile = base64.b64decode(icc_profile)
            with BytesIO(icc_profile) as f:
                icc_profile = ImageCms.ImageCmsProfile(f)
            return lambda img: ImageCms.profileToProfile(img, icc_profile
          return lambda img: None

    def transform(self, tile):
        if tile.mode == 'RGBA':
            background = Image.new('RGBA', tile.size, (255, 255, 255))
            tile = Image.alpha_composite(background, tile)
            tile = tile.convert('RGB')
        self._get_transform(tile)


class _SlideFile:
    def __init__(self, relpath):
        self.name = os.path.basename(relpath)
        self.url_path = relpath


class _Directory:
    def __init__(self, basedir, relpath=''):
        self.name = os.path.basename(relpath)
        self.children = []
        for name in sorted(os.listdir(os.path.join(basedir, relpath))):
            cur_relpath = os.path.join(relpath, name)
            cur_path = os.path.join(basedir, cur_relpath)
            if os.path.isdir(cur_path):
                cur_dir = _Directory(basedir, cur_relpath)
                if cur_dir.children:
                    self.children.append(cur_dir)
            elif self._is_supported_format(cur_path):
                self.children.append(_SlideFile(cur_relpath))

    def _is_supported_format(self, path):
        supported_formats = ['.svs', '.tiff', '.tif', '.jpeg', '.jpg', '.png']
        ext = os.path.splitext(path)[1].lower()
        if ext in supported_formats:
            if OpenSlide.detect_format(path) or ext in ['.jpeg', '.jpg', '.png', '.tiff', '.tif']:
                return True
        return False


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument(
        'slides', nargs='?', default='.',
        help='directory to serve (default: current directory)',
    )
    parser.add_argument(
        '--host', default='127.0.0.1',
        help='host address (default: 127.0.0.1)',
    )
    parser.add_argument(
        '--port', type=int, default=5000,
        help='port to serve on (default: 5000)',
    )
    args = parser.parse_args()
    app = create_app({
        'SLIDE_DIR': args.slides,
    })
    app.run(host=args.host, port=args.port)

