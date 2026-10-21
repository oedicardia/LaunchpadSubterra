"""
Launchpads editing the same clip together in the melodic step sequencer, as
one bigger grid. Settings.TEAM_POSITION_BY_SLOT gives each one its (column,
row); (0, 0) is the top left one, the master.

What a team shows on a clip is kept as one frame: what the master shows.
Each Launchpad in use shows the frame moved by its place among the
Launchpads in use on that clip:
- one column to the right: the next loop block, same octave
- one row down: same loop block, the octave (drumrack: pitch block) below;
  in focus mode the focus pitch one Launchpad (FOCUS_ROWS semitones) lower
Only the Launchpads in use count: one alone shows the frame itself, i.e.
behaves as the master; with only the right column in use, its top one shows
the master's loop block and octave; with only the top right and bottom left
ones, the top right one has the master's octave, the bottom left one its
loop block.

A change made on one Launchpad (loop block, octave, focus pitch, resolution,
absolute or drumrack mode) moves the frame so that this Launchpad shows what
was asked, then the others follow. The frame is kept so that every Launchpad
in use stays in range: e.g. selecting the first loop block on a right one
while a left one is in use selects the second block (the left one shows the
first), selecting the highest octave on a bottom one while a top one is in
use selects the second highest. Focus mode is the exception: the focus pitch
stays on the note it was asked for, and the rows another Launchpad then has
beyond the MIDI range show a chessboard and do nothing. The clip's tag and
the JSON file store the frame.

A Launchpad is in use when its melodic step sequencer is on, with a clip.
A clip's frame lives while a Launchpad is in use on it: the first one on a
clip takes it from the clip's tag.
"""
from .SequencerConstants import MELODIC_OCTAVE_COUNT, DRUM_BLOCK_COUNT

# Focus mode is melodic only: 7 note rows, one semitone each
FOCUS_ROWS = 7
# Frame keys every Launchpad of a team uses as they are
CLIP_SETTINGS = ('resolution_index', 'is_absolute', 'is_drumrack')


class StepSeqTeam(object):

	def __init__(self):
		# One place per Launchpad of a team: {'member', 'column', 'row', 'clip'},
		# 'clip' = the clip it is in use on, None when not in use
		self._places = []
		# One frame per clip in use (see the module doc): the CLIP_SETTINGS,
		# 'block', 'octave', 'focus' (None out of focus mode) and 'clip'
		self._frames = []
		# True while a frame is being shown: the Launchpads then save nothing,
		# the one the change came from does
		self._showing = False

	def add(self, member, position):
		""" member: StepSequencerComponent2 of the Launchpad at position (column, row) """
		column, row = position
		self._places.append({'member': member, 'column': column, 'row': row, 'clip': None})

	def remove(self, member):
		place = self._place(member)
		if place is not None:
			# Gone first, so the others never show anything through it
			self._places.remove(place)
			self._leave(place)

	def is_showing(self):
		return self._showing

	def frame_values(self, clip, settings):
		""" Tag / JSON settings with the octave and loop block of clip's frame, if any """
		frame = self._frame(clip)
		if frame is not None:
			settings['display_octave'] = frame['octave']
			settings['loop_block'] = frame['block']
			settings['loop_page_offset'] = frame['block'] // 8
		return settings

	def changed(self, member):
		""" member changed what it shows or a clip setting: the others follow """
		place = self._place(member)
		if place is None or self._showing or place['clip'] is None:
			return
		if not self._same_clip(place['clip'], member.team_clip()):
			return
		group = self._group(place['clip'])
		frame = self._frame(place['clip'])
		self._move_frame(frame, place, group, member.team_state())
		self._show(frame, group)

	def update(self, member):
		""" member's step sequencer was switched on or off, or changed clip """
		place = self._place(member)
		if place is None:
			return
		clip = member.team_clip()
		if self._same_clip(place['clip'], clip):
			return
		self._leave(place)
		if clip is None:
			return
		place['clip'] = clip
		group = self._group(clip)
		frame = self._frame(clip)
		if frame is None:
			# First one on this clip: the frame comes from the clip's tag
			frame = {'clip': clip}
			self._frames.append(frame)
			self._move_frame(frame, place, group, member.team_tag_state())
		self._settle(frame, group)

	def _leave(self, place):
		clip = place['clip']
		if clip is None:
			return
		place['clip'] = None
		group = self._group(clip)
		frame = self._frame(clip)
		if group:
			self._settle(frame, group)
		elif frame is not None:
			self._frames.remove(frame)

	def _settle(self, frame, group):
		""" Show frame after a Launchpad joined or left group; saved if it had to move """
		before = dict(frame)
		self._limit(frame, group)
		self._show(frame, group)
		if frame != before:
			group[0]['member'].team_save()

	def _offsets(self, place, group):
		""" (loop blocks to the right, rows of Launchpads down) of place in group """
		return (place['column'] - min(p['column'] for p in group),
		        place['row'] - min(p['row'] for p in group))

	def _move_frame(self, frame, place, group, state):
		""" Set frame so that place shows state (see team_state), within the limits """
		block_offset, band_offset = self._offsets(place, group)
		for key in CLIP_SETTINGS:
			frame[key] = state[key]
		frame['block'] = state['block'] - block_offset
		frame['octave'] = state['octave'] + band_offset
		focus = state['focus']
		frame['focus'] = None if focus is None else focus + band_offset * FOCUS_ROWS
		self._limit(frame, group)

	def _limit(self, frame, group):
		"""
		Keep frame so that every Launchpad of group shows loop blocks and
		octaves in range. The focus pitch is left where it was asked: rows
		of another Launchpad beyond the MIDI range show a chessboard.
		"""
		columns = max(p['column'] for p in group) - min(p['column'] for p in group)
		rows = max(p['row'] for p in group) - min(p['row'] for p in group)
		block_count = group[0]['member'].team_block_count(frame['resolution_index'])
		frame['block'] = max(0, min(frame['block'], block_count - 1 - columns))
		if frame['is_drumrack']:
			lowest, highest = 0, DRUM_BLOCK_COUNT - 1
		else:
			lowest, highest = -1, MELODIC_OCTAVE_COUNT - 2
		frame['octave'] = max(lowest + rows, min(frame['octave'], highest))

	def _show(self, frame, group):
		self._showing = True
		try:
			for place in group:
				block_offset, band_offset = self._offsets(place, group)
				state = dict(frame)
				state['block'] = frame['block'] + block_offset
				state['octave'] = frame['octave'] - band_offset
				if frame['focus'] is not None:
					state['focus'] = frame['focus'] - band_offset * FOCUS_ROWS
				place['member'].team_show(state)
		finally:
			self._showing = False

	def _place(self, member):
		for place in self._places:
			if place['member'] is member:
				return place
		return None

	def _group(self, clip):
		""" Places in use on clip, the master first (top row, then left column first) """
		group = [p for p in self._places if self._same_clip(p['clip'], clip)]
		return sorted(group, key=lambda p: (p['row'], p['column']))

	def _frame(self, clip):
		for frame in self._frames:
			if self._same_clip(frame['clip'], clip):
				return frame
		return None

	@staticmethod
	def _same_clip(a, b):
		if a is None or b is None:
			return a is b
		try:
			return a == b
		except Exception:
			# a clip deleted in Live meanwhile
			return False


# Shared by all the Launchpads running this script (same module)
TEAM = StepSeqTeam()
