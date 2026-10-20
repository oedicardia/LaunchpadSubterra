
# ==========================================================
# Step Sequencer Modes
# ==========================================================

STEPSEQ_MODE_NORMAL = 1
STEPSEQ_MODE_MULTINOTE = 2

# ==========================================================
# Melodic Note Editor Modes
# ==========================================================

STEPSEQ_MODE_NOTES = 1
STEPSEQ_MODE_NOTES_OCTAVES = 2

STEPSEQ_MODE_COPY_PASTE = 5

STEPSEQ_MODE_STEP_VELOCITY_EDITOR = 6
STEPSEQ_MODE_STEP_LENGTH_EDITOR = 7

STEPSEQ_MODE_VERTICAL_VELOCITY = 8
STEPSEQ_MODE_VERTICAL_LENGTH = 9

STEPSEQ_MODE_SCALE_EDIT = 10

STEPSEQ_MODE_NAV_OVERVIEW = 11   # Choose a unique ID not used by other modes
# ==========================================================
# Resolutions
# ==========================================================
RESOLUTION_INIT = 4

RESOLUTION_MAP = [
    0.015625,   # 1/256
    0.03125,    # 1/128
    0.0625,    # 1/64
    0.125,     # 1/32
    0.25,   # 1/16
    0.5,    # 1/8
    1.0,    # 1/4
    2.0     # 1/2
]

RESOLUTION_NAMES = [
    "1/256",
    "1/128",
    "1/64",
    "1/32",
    "1/16",
    "1/8",
    "1/4",
    "1/2"
]

# Resolution button: tap = zoom in, double tap = zoom out, long press =
# middle resolution. Max time from the first tap's release to the second
# tap's press (seconds), how many scheduler ticks the single-tap zoom in
# waits before running, and the hold time of a long press (seconds).
RESOLUTION_DOUBLE_TAP_WINDOW = 0.3
RESOLUTION_DOUBLE_TAP_TICKS = 3
RESOLUTION_LONG_PRESS = 0.5

# ==========================================================
# Velocity of new notes
# ==========================================================
# New notes take the last value received on this CC. Channels are 0-based:
# 11 = MIDI channel 12. Until the CC has been received, the default is used.
NOTE_VELOCITY_CC_CHANNEL = 11
NOTE_VELOCITY_CC_NUMBER = 16
DEFAULT_NOTE_VELOCITY = 90

# ==========================================================
# Velocity / Length Slider Subdivisions
# ==========================================================
# Holding the last lit pad of a velocity or length slider and tapping the pad
# next to it moves the value by 1/SLIDER_SUBDIVISIONS of the gap between two
# pads: 4 values in between, then the value of the next pad. The last lit pad
# shows Sub1..Sub4 for the in-between value closest to the notes' value, and
# the normal On colour when the value is the pad's own value.
SLIDER_SUBDIVISIONS = 5
VELOCITY_SUB_COLORS = [
    "StepSequencer2.Velocity.Sub1",
    "StepSequencer2.Velocity.Sub2",
    "StepSequencer2.Velocity.Sub3",
    "StepSequencer2.Velocity.Sub4",
]
LENGTH_SUB_COLORS = [
    "StepSequencer2.Length.Sub1",
    "StepSequencer2.Length.Sub2",
    "StepSequencer2.Length.Sub3",
    "StepSequencer2.Length.Sub4",
]

# ==========================================================
# Drumrack Pitch Blocks / Navigation View
# ==========================================================
# In drumrack mode the vertical navigation unit is a "pitch block":
# 8 contiguous chromatic notes. Block b covers MIDI [b*8 .. b*8+7],
# with b*8 on row 7 (bottom) and b*8+7 on row 0 (top).
# MIDI 0..127 -> 16 blocks (0..15).
DRUM_BLOCK_SIZE = 8
DRUM_BLOCK_COUNT = 16
# Melodic mode: 11 octave bands (MIDI 0..127 -> octaves 0..10)
MELODIC_OCTAVE_COUNT = 11
# Number of bands visible at once in the navigation view
NAV_VISIBLE_BANDS = 8
# Navigation view double tap: max time between the two releases (seconds),
# and how many scheduler ticks (~100 ms each) a single tap waits before running.
NAV_DOUBLE_TAP_WINDOW = 0.3
NAV_DOUBLE_TAP_TICKS = 3
# Navigation button in the notes view: tap = navigation view, double tap =
# back to the previous position. Max time from the first tap's release to
# the second tap's press (seconds), and how many scheduler ticks the
# single tap waits before running.
NAV_BUTTON_DOUBLE_TAP_WINDOW = 0.3
NAV_BUTTON_DOUBLE_TAP_TICKS = 3
# Clip gestures (Toggle held + Copy taps): max time from the first tap's
# release to the second tap's press (seconds), and how many scheduler ticks
# the single-tap duplicate waits before running.
CLIP_GESTURE_TAP_WINDOW = 0.3
CLIP_GESTURE_TAP_TICKS = 3
# Loop edits made in Live: coalesce the clip-tag rewrite (scheduler ticks)
LOOP_TAG_SYNC_DELAY_TICKS = 5
# Drumrack: switching Live's Scale toggle is deferred (Live forbids changes
# from inside notifications)
SCALE_MODE_DELAY_TICKS = 1

# ==========================================================
# Drumrack Note Editor Colours (index = MIDI pitch % 16)
# ==========================================================
_PITCH_DM = "StepSequencer2.PitchDM."
DRUM_COLORS_NO_NOTE = [
    _PITCH_DM + "KickOff",  _PITCH_DM + "HHOff",  _PITCH_DM + "SnareOff", _PITCH_DM + "HHOff",    # 0-3
    _PITCH_DM + "KickOff",  _PITCH_DM + "HHOff",  _PITCH_DM + "SnareOff", _PITCH_DM + "HHOff",    # 4-7
    _PITCH_DM + "FX1Off",   _PITCH_DM + "FX1Off", _PITCH_DM + "FX1Off",   _PITCH_DM + "FX1Off",   # 8-11
    _PITCH_DM + "FX2Off",   _PITCH_DM + "FX2Off", _PITCH_DM + "FX2Off",   _PITCH_DM + "FX2Off",   # 12-15
]
DRUM_COLORS_NOTE = [
    _PITCH_DM + "KickOn",   _PITCH_DM + "HHOn",   _PITCH_DM + "SnareOn",  _PITCH_DM + "HHOn",     # 0-3
    _PITCH_DM + "KickOn",   _PITCH_DM + "HHOn",   _PITCH_DM + "SnareOn",  _PITCH_DM + "HHOn",     # 4-7
    _PITCH_DM + "FX1On",    _PITCH_DM + "FX1On",  _PITCH_DM + "FX1On",    _PITCH_DM + "FX1On",    # 8-11
    _PITCH_DM + "FX2On",    _PITCH_DM + "FX2On",  _PITCH_DM + "FX2On",    _PITCH_DM + "FX2On",    # 12-15
]
DRUM_METRONOME_NO_NOTE = "StepSequencer2.NoteEditor.MetronomeInPageDR"
DRUM_METRONOME_NOTE = "StepSequencer2.NoteEditor.PlayInPageDR"
