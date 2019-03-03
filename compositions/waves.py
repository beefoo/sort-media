# -*- coding: utf-8 -*-

import argparse
import inspect
import math
import numpy as np
import os
from pprint import pprint
import random
import sys

# add parent directory to sys path to import relative modules
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0,parentdir)

from lib.audio_mixer import *
from lib.audio_utils import *
from lib.clip import *
from lib.collection_utils import *
from lib.composition_utils import *
from lib.io_utils import *
from lib.math_utils import *
from lib.sampler import *
from lib.statistics_utils import *
from lib.video_utils import *

# input
parser = argparse.ArgumentParser()
addVideoArgs(parser)
parser.add_argument('-translate', dest="TRANSLATE_AMOUNT", default=0.8, type=float, help="Amount to translate clip as a percentage of minimum dimension")
parser.add_argument('-scale', dest="SCALE_AMOUNT", default=1.33, type=float, help="Amount to scale clip")
parser.add_argument('-grid', dest="GRID", default="256x256", help="Size of grid")
parser.add_argument('-grid1', dest="END_GRID", default="32x32", help="End size of grid")
parser.add_argument('-steps', dest="STEPS", default=16, type=int, help="Number of waves/beats")
parser.add_argument('-wd', dest="WAVE_DUR", default=8000, type=int, help="Wave duration in milliseconds")
parser.add_argument('-bd', dest="BEAT_DUR", default=6000, type=int, help="Beat duration in milliseconds")
parser.add_argument('-maxa', dest="MAX_AUDIO_CLIPS", default=4096, type=int, help="Maximum number of audio clips to play")
parser.add_argument('-keep', dest="KEEP_FIRST_AUDIO_CLIPS", default=256, type=int, help="Ensure the middle x audio files play")
a = parser.parse_args()
parseVideoArgs(a)
makeDirectories([a.OUTPUT_FRAME, a.OUTPUT_FILE, a.CACHE_DIR])

# parse arguments
START_GRID_W, START_GRID_H = tuple([int(v) for v in a.GRID.strip().split("x")])
END_GRID_W, END_GRID_H = tuple([int(v) for v in a.END_GRID.strip().split("x")])
GRID_W, GRID_H = (max(START_GRID_W, END_GRID_W), max(START_GRID_H, END_GRID_H))
ZOOM_DUR = a.STEPS * a.BEAT_DUR
ZOOM_EASE = "cubicInOut"

# Get video data
startTime = logTime()
stepTime = startTime
samples, sampleCount, container, sampler, stepTime, cCol, cRow = initGridComposition(a, GRID_W, GRID_H, stepTime)

for i, s in enumerate(samples):
    # play in order: center first, clockwise
    samples[i]["angleFromCenter"] = angleBetween(cCol, cRow, s["col"], s["row"])
    # calculate translate distance
    translateDistance = min(s["width"], s["height"]) * a.TRANSLATE_AMOUNT
    samples[i]["translateAmount"] = translatePoint(0, 0, translateDistance, samples[i]["angleFromCenter"])
    # randomized volume multiplier
    samples[i]["volumeMultiplier"] = pseudoRandom(a.RANDOM_SEED+i, range=(0.33, 1.0))

samples = sorted(samples, key=lambda s: (s["distanceFromCenter"], s["angleFromCenter"]))
samples = addIndices(samples, "playOrder")
samples = addNormalizedValues(samples, "playOrder", "nPlayOrder")
samples = addNormalizedValues(samples, "power", "nPower")

# add audio clip properties
for i, s in enumerate(samples):
    samples[i].update({
        "zindex": sampleCount-i,
        "volume": lerp(a.VOLUME_RANGE, (1.0 - s["nDistanceFromCenter"]) * s["volumeMultiplier"])
    })

stepTime = logTime(stepTime, "Calculate clip properties")

# start with everything with minimum alpha
for i, s in enumerate(samples):
    samples[i]["alpha"] = a.ALPHA_RANGE[0]

clips = samplesToClips(samples)
stepTime = logTime(stepTime, "Samples to clips")

for i, clip in enumerate(clips):
    clip.vector.setParent(container.vector)

ms = a.PAD_START
fromScale = 1.0 * GRID_W / START_GRID_W
toScale = 1.0 * GRID_W / END_GRID_W
container.queueTween(ms, ZOOM_DUR, ("scale", fromScale, toScale, ZOOM_EASE))

for step in range(a.STEPS):
    nstep = 1.0 * step / a.STEPS

     # temporarily set scale so we can calculate clip visibility for playing audio
    nzoom = ease(nstep, ZOOM_EASE)
    currentScale = lerp((fromScale, toScale), nzoom)
    container.vector.setTransform(scale=(currentScale, currentScale))

    # play kick
    sampler.queuePlay(ms, "kick", index=step, params={
        "volume": 1.5
    })

    visibleClips = [clip for clip in clips if clip.vector.isVisible(a.WIDTH, a.HEIGHT)]
    visibleClipCount = len(visibleClips)

    # play and render waves
    for i, clip in enumerate(visibleClips):
        nprogress = 1.0 * i / visibleClipCount
        clipStartMs = ms + roundInt(a.WAVE_DUR * nprogress)

        # play clip
        if clip.props["playAudio"]:
            clip.queuePlay(clipStartMs, {
                "dur": clip.props["audioDur"],
                "volume": clip.props["volume"],
                "fadeOut": clip.props["fadeOut"],
                "fadeIn": clip.props["fadeIn"],
                "pan": clip.props["pan"],
                "reverb": clip.props["reverb"]
            })

        # move the clip outward then back inward, alpha up then down
        alphaFrom = lerp(a.ALPHA_RANGE, ease(1.0 - clip.props["nDistanceFromCenter"]))
        alphaTo = a.ALPHA_RANGE[0]
        renderDur = clip.props["dur"]
        halfLeft = int(renderDur / 2)
        halfRight = (renderDur - halfLeft) * 2
        tx, ty = clip.props["translateAmount"]
        clip.queueTween(clipStartMs, halfLeft, [
            ("translateX", 0, tx, "sin"),
            ("translateY", 0, ty, "sin"),
            ("alpha", alphaTo, alphaFrom, "sin"),
            ("scale", 1.0, a.SCALE_AMOUNT, "sin")
        ])
        clip.queueTween(clipStartMs+halfLeft, halfRight, [
            ("translateX", tx, 0, "sin"),
            ("translateY", ty, 0, "sin"),
            ("alpha", alphaFrom, alphaTo, "sin"),
            ("scale", a.SCALE_AMOUNT, 1.0, "sin")
        ])

    # ms += halfBeatDur
    # play snare
    # sampler.queuePlay(ms, "snare", index=step)

    ms += a.BEAT_DUR

ms += max(0, a.WAVE_DUR-a.BEAT_DUR) # add the remainder from the wave

# sort frames
container.vector.sortFrames()
for clip in clips:
    clip.vector.sortFrames()

# reset scale
container.vector.setTransform(scale=(1.0, 1.0))
stepTime = logTime(stepTime, "Created video clip sequence")

processComposition(a, clips, ms, sampler, stepTime, startTime)
