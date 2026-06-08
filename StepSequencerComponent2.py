from _Framework.ControlSurfaceComponent import ControlSurfaceComponent
from _Framework.ButtonMatrixElement import ButtonMatrixElement
from .StepSequencerComponent import StepSequencerComponent, ButtonElement
from .SequencerConstants import RESOLUTION_NAMES
from .LoopSelectorComponent import LoopSelectorComponent
from .NoteSelectorComponent import NoteSelectorComponent
from .ScaleComponent import MUSICAL_MODES, KEY_NAMES
from .TrackControllerComponent import TrackControllerComponent
from random import randrange
import time
from random import uniform

STEPSEQ_MODE_NOTES = 1
STEPSEQ_MODE_NOTES_OCTAVES = 2
# STEPSEQ_MODE_NOTES_VELOCITIES = 3
# STEPSEQ_MODE_NOTES_LENGTHS = 4
STEPSEQ_MODE_COPY_PASTE = 5
STEPSEQ_MODE_STEP_VELOCITY_EDITOR = 6 	# Horizontal, step Velocity Mode
STEPSEQ_MODE_STEP_LENGTH_EDITOR = 7
STEPSEQ_MODE_VERTICAL_VELOCITY = 8     # Vertical Column Velocity Mode
STEPSEQ_MODE_VERTICAL_LENGTH = 9     # Vertical Column length Mode

LONG_BUTTON_PRESS = 1.0

# TODO :
# extend / clear region (possible via drum step seq for now)
# not even clip lengths (using shift notes ?)
# store scale settings per clip or track ?
# display current scale mode in osd


class MelodicNoteEditorComponent(ControlSurfaceComponent):

	def __init__(self, step_sequencer, matrix, side_buttons, control_surface):
		self._initializing = True
		ControlSurfaceComponent.__init__(self)
		self._control_surface = control_surface
		self.set_enabled(False)
		#self._loop_page_offset = 0

		self._step_sequencer = step_sequencer

		# ths needs to be right after self._step_sequencer = step_sequencer
		self._clip_slot = None

		self._matrix = matrix
		self._side_buttons = side_buttons

		# buttons
		#self._matrix = None

		# matrix
		self.set_matrix(matrix)
		self._grid_buffer = [[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], 
		[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], 
		[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0]]
		self._grid_back_buffer = [[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0],
		[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], 
		[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0]]

		# debug
		# self._control_surface.log_message("MATRIX ASSIGNED")
		# init
		#self._initializing = True


		# clip (rest)
		self._clip = None
		self._playhead = None
		self._note_cache = []
		self._force_update = True
		self.song().view.add_selected_track_listener(self._on_selected_track_changed)
		self.song().view.add_selected_scene_listener(self._on_selected_scene_changed)
		self._register_clip_slot_listener()

		self._init_data()

		self._velocity_map = [0, 18, 36, 54, 72, 90, 108,127]
		self._length_map = [0.5, 1, 2, 3, 4, 8, 16, 32]

		# time
		self._playhead = 0
		self._page = 0

		# notes
		self._key_indexes = [36, 37, 38, 39, 40, 41, 42, 43]
		self._key_index_is_in_scale = [True, False, True, True, False, True, False, True]
		self._key_index_is_root_note = [True, False, False, False, False, False, False, False]
		self._is_monophonic = False

		# quantization
		self._quantization = 16

		# Resolution
		self._resolution = 16

		# MODE
		self._mode = STEPSEQ_MODE_NOTES

		# buttons
		self._clip_toggle_button = None
		self.set_clip_toggle_button(self._side_buttons[2])

		self._mode_copy_paste_button = None
		self.set_mode_copy_paste_button(self._side_buttons[4])
		self._is_copy_paste_shifted = False
		self._last_copy_paste_button_press = time.time()


		self._mode_notes_lengths_button = None
		self.set_mode_notes_lengths_button(self._side_buttons[4])
		self._is_notes_lengths_shifted = False
		self._last_notes_lengths_button_press = time.time()

		self._mode_notes_octaves_button = None
		self.set_mode_notes_octaves_button(self._side_buttons[6])
		self._is_octave_shifted = False
		self._last_notes_octaves_button_press = time.time()

		self._mode_notes_velocities_button = None
		self.set_mode_notes_velocities_button(self._side_buttons[5])
		self._is_notes_velocity_shifted = False
		self._last_notes_velocity_button_press = time.time()

		# self._mode_notes_pitches_button = None
		# self.set_mode_notes_pitches_button(self._side_buttons[7])
		# self._is_notes_pitches_shifted = False
		# self._last_notes_pitches_button_press = time.time()

		# self._mode_zoom_button = None
		# self.set_mode_zoom_button(self._side_buttons[7])
		# self._is_zoom_shifted = False
		# self._last_zoom_button_press = time.time()

		self._is_velocity_shifted = False
		self._is_mute_shifted = False

		# disable the lock but allow the code to be used in the future if desired
		self._is_locked = False  # Add this line
		self._lock_to_track = False  # Add this line

		# end init
		self._initializing = False

	def disconnect(self):
		self._remove_highlighted_clip_slot_listener()
		self._remove_clip_slot_listener()
		self._step_sequencer = None
		self._matrix = None
		self._mode_notes_lengths_button = None
		self._mode_notes_octaves_button = None
		self._mode_notes_velocities_button = None
		self._mode_notes_pitches_button = None
		self._notes_pitches = None
		self._notes_velocities = None
		self._notes_octaves = None
		self._notes_lengths = None
		self._clip = None
	
	
	def _remove_scale_listeners(self):
		try:
			self.song().remove_root_note_listener(self.handle_root_note_changed)
		except RuntimeError:
			pass
		try:
			self.song().remove_scale_name_listener(self.handle_scale_name_changed)
		except RuntimeError:
			pass
		
	
	def _register_scale_listeners(self):
		try:
			self.song().add_root_note_listener(self.handle_root_note_changed)
		except RuntimeError:
			pass
		try:
			self.song().add_scale_name_listener(self.handle_scale_name_changed)
		except RuntimeError:
			pass

	def handle_root_note_changed(self):
		self._scale_selector.set_key(self.song().root_note, False, True)
		self.update()


	def handle_scale_name_changed(self):
		self._scale_selector.set_modus(self._scale_selector._modus_names.index(self.song().scale_name), False, True)
		self.update()
		
		

	def set_enabled(self, enabled):
		ControlSurfaceComponent.set_enabled(self, enabled)
		# debug
		# self._control_surface.log_message(
		# 	f"{self.__class__.__name__} enabled={enabled}"
		# )

		if not enabled:
			self._remove_scale_listeners()
		else:
			self._register_scale_listeners()

	def _init_data(self):
		pages = 1024
		self._editing_step = None
		self._editing_step_pitches = []
		self._pending_velocity_editor = False
		self._velocity_wait_animation = False
		self._velocity_wait_start_times = [0] * 8
		self._pending_length_editor = False
		self._length_wait_animation = False
		self._length_wait_start_times = [0] * 8
		self._is_velocity_editor_vertical = False  # To track if we are in vertical mode
		self._is_length_editor_vertical = False    # To track if we are in vertical mode
		self._notes_pitches = [0] * (7 * pages)
		self._notes_velocities = [4] * pages
		self._display_octave = 2
		self._notes_octaves = [2] * pages
		self._notes_lengths = [3] * pages

	def set_mode(self, mode):
		self._control_surface.log_message(
			"SET_MODE old=%s new=%s" % (self._mode, mode)
		)
		self._mode = mode
		self._force_update = True
		self._control_surface.log_message(
			"CALLING UPDATE FROM SET_MODE"
		)
		self.update()

	def set_clip(self, clip):
		if self._clip != clip:
			self._init_data()
			self._clip = clip
			#self._register_clip_slot_listener() --> removed because it empties top clip of selected track during initialization

	def set_note_cache(self, note_cache):
		if self._note_cache != note_cache:
			self._note_cache = note_cache
			self._parse_notes()

	def set_playhead(self, playhead):
		self._playhead = playhead
		self._update_matrix()

	def set_multinote(self, x=0, y=0):
		pass

	@property
	def resolution(self):
		return self._resolution

	def set_resolution(self, resolution):

		#old_resolution = self._resolution
		self._resolution = resolution
		self._parse_notes()
		self._update_matrix()

		#for i in range(8):
			# self._control_surface.log_message(
			# 	" - - - - - in set_resolution  - - - - - " + str(self._get_notes_at_step(i)))
		# update loop point
		#if self._clip != None and old_resolution != self._resolution:
			#
			# self._loop_start = int(
			# 	self._clip.loop_start *
			# 	self._resolution /
			# 	old_resolution
			# )
			#
			# self._loop_end = int(
			# 	self._clip.loop_end *
			# 	self._resolution /
			# 	old_resolution
			# )
			#
			# # safety
			# if self._loop_end <= self._loop_start:
			# 	self._loop_end = self._loop_start + 1
			#
			# try:
			# 	self._clip.loop_start = self._loop_start
			# 	self._clip.loop_end = self._loop_end
			#
			# 	self._clip.start_marker = self._loop_start
			# 	self._clip.end_marker = self._loop_end

			# except RuntimeError:
			# 	pass

			# IMPORTANT:
			# do not rewrite notes during controller init
			# if not self._initializing:
			# 	self._update_clip_notes()

	@property
	def quantization(self):
		return self._quantization

	def set_quantization(self, quantization):

		old_quantize = self._quantization
		self._quantization = quantization

		# update loop point
		if self._clip != None and old_quantize != self._quantization:

			self._loop_start = int(
				self._clip.loop_start *
				self._quantization /
				old_quantize
			)

			self._loop_end = int(
				self._clip.loop_end *
				self._quantization /
				old_quantize
			)

			# safety
			if self._loop_end <= self._loop_start:
				self._loop_end = self._loop_start + 1

			try:
				self._clip.loop_start = self._loop_start
				self._clip.loop_end = self._loop_end

				self._clip.start_marker = self._loop_start
				self._clip.end_marker = self._loop_end

			except RuntimeError:
				pass

			# IMPORTANT:
			# do not rewrite notes during controller init
			if not self._initializing:
				self._update_clip_notes()

	def set_diatonic(self, diatonic):
		self._diatonic = diatonic

	def set_key_indexes(self, key_indexes):			# debug
		#self._control_surface.log_message(
		#">>>>>>>>>>>> set_key_indexes called, key_indexes = "+str(key_indexes))
		if self._key_indexes != key_indexes:
			self._key_indexes = key_indexes
			#self._update_clip_notes()

	def set_key_index_is_in_scale(self, key_index_is_in_scale):
		self._key_index_is_in_scale = key_index_is_in_scale

	def set_key_index_is_root_note(self, key_index_is_root_note):
		self._key_index_is_root_note = key_index_is_root_note

	def set_page(self, page):
		self._page = page

	def _get_notes_at_step(self, idx):
		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		return [note for note in self._note_cache if start_time <= note[1] < end_time]

	def _get_note_for_pitch_at_step(self, idx, pitch):

		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		for note in self._note_cache:

			if (
					start_time <= note[1] < end_time
					and note[0] == pitch
			):
				return note

		return None


	def _parse_notes(self):
		# clear notes
		for i in range(len(self._notes_pitches)):
			self._notes_pitches[i] = 0

		first_note = [True] * len(self._notes_velocities)
		# self._control_surface.log_message(
		#  	">>>>>>>>>> self._note_cache=%s" % str(self._note_cache))
		for note in self._note_cache:

			note_key = note[0]
			note_position = note[1]
			note_length = note[2]
			note_velocity = note[3]
			note_muted = note[4]

			if note_muted:
				continue

			i = int(note_position / self._resolution)

			#
			# velocity/length only once per step
			#
			if first_note[i]:
				first_note[i] = False

				for x in range(7):
					if note_velocity >= self._velocity_map[x]:
						self._notes_velocities[i] = x

				for x in range(7):
					if note_length * 4 >= self._length_map[x] * self._resolution:
						self._notes_lengths[i] = x

			#
			# pitch display for EVERY note
			#
			for j in range(min(7, len(self._key_indexes))):

				display_pitch = (
						self._key_indexes[j]
						+ 12 * (self._display_octave - 2)
				)

				if note_key == display_pitch:
					self._notes_pitches[i * 7 + j] = 1
# 		for note in self._note_cache:
# 			note_position = note[1]
# 			note_key = note[0]
# 			note_length = note[2]
# 			note_velocity = note[3]
# 			note_muted = note[4]
# #			i = int(note_position / self._quantization)
# 			i = int(note_position / self._resolution)
# 			#self._control_surface.log_message("note_position=%s resolution=%s i=%s" % (note_position, self._resolution, i))
# 			if not note_muted:
# 				#self._control_surface.log_message(
# 				#	">>>>>>>>>> note_mute=False, first_note=%s, resolution=%s, i=%s" % (first_note, self._resolution, i))
# 				if first_note[i]:
# 					first_note[i] = False
#
# 					# velocity
# 					for x in range(7):
# 						if note_velocity >= self._velocity_map[x]:
# 							self._notes_velocities[i] = x
#
# 					# length
# 					for x in range(7):
# 						if note_length * 4 >= self._length_map[x] * self._resolution:
# 						#if note_length * 4 >= self._length_map[x] * self._quantization:
# 							self._notes_lengths[i] = x
#
# 					# note and octave
# 					found = False
# 					for j in range(min(7, len(self._key_indexes))):#was max
# 						display_pitch = (self._key_indexes[j] + 12 * (self._display_octave - 2))
# 						if note_key == display_pitch:
# 							self._notes_pitches[i * 7 + j] = 1
# 				# elif not self._is_monophonic:
# 				# 	# note
# 				# 	found = False
# 				# 	for j in range(min(7, len(self._key_indexes))): #was max
# 				# 		if note_key == self._key_indexes[j] + 12 * (self._notes_octaves[i] - 2) and not found:
# 				# 			found = True
# 				# 			self._notes_pitches[i * 7 + j] = 1
# 		self._update_matrix()

	def _toggle_note_at_grid_position(self, idx, y):

		grid_time = idx * self._resolution

		pitch = (self._key_indexes[6 - y] + 12 * (self._display_octave - 2))

		notes = list(self._note_cache)

		for note in notes:
			if (
					note[0] == pitch and
					abs(note[1] - grid_time) < 0.0001
			):
				notes.remove(note)

				self._clip.select_all_notes()
				self._clip.replace_selected_notes(tuple(notes))

				return

		notes.append(
			(
				pitch,
				grid_time,
				self._resolution,
				100,
				False
			)
		)

		self._clip.select_all_notes()
		self._clip.replace_selected_notes(tuple(notes))
	# 	grid_time = idx * self._resolution
	#
	# 	pitch = self._key_indexes[6 - y] + \
	# 	        12 * (self._notes_octaves[idx] - 2)
	#
	# 	notes = list(self._note_cache)
	#
	# 	found = False
	#
	# 	for note in notes:
	# 		if note.pitch == pitch and note.time == grid_time:
	# 			notes.remove(note)
	# 			found = True
	# 			break
	#
	# 	if not found:
	# 		notes.append(
	# 			[pitch,
	# 			 grid_time,
	# 			 default_length,
	# 			 default_velocity,
	# 			 False]
	# 		)
	#
	# 	write_notes_to_clip(notes)

	def _write_note_cache_to_clip(self, note_cache):
		if self._clip is None:
			return

		self._clip.select_all_notes()
		self._clip.replace_selected_notes(tuple(note_cache))


	def _update_clip_notes(self):
		if self._initializing:
			return
		if self._clip != None and self._step_sequencer.is_enabled():
			note_cache = list()
			for x in range(len(self._notes_velocities)):
				for note_index in range(7):
					if self._notes_pitches[x * 7 + note_index] == 1:
						time = x * self._resolution
						#time = x * self._quantization
						velocity = self._velocity_map[self._notes_velocities[x]]
						length = self._length_map[self._notes_lengths[x]] * self._resolution / 4.0
						#length = self._length_map[self._notes_lengths[x]] * self._quantization / 4.0
						pitch = self._key_indexes[note_index] + 12 * (self._notes_octaves[x] - 2)
						if(pitch >= 0 and pitch < 128 and velocity >= 0 and velocity < 128 and length >= 0):
							note_cache.append([pitch, time, length, velocity, False])
			self._clip.select_all_notes()
			# debug
			# self._control_surface.log_message(
			# ">>>>>>>>>>>> _update_clip_notes called, cache size=%d" %
			# len(self._note_cache))
			# self._control_surface.log_message(
			# 	"clip notes about to be written"
			# )

			self._clip.replace_selected_notes(tuple(note_cache)) # Todo : deprecated
			#self._control_surface.schedule_message(1, self._sch_update, ([self._clip,tuple(note_cache)]))

	def _sch_update(self, data):
			clip = data[0]
			note_cache = data[1]
			clip.select_all_notes()
			if(note_cache == None):
				# debug
				# self._control_surface.log_message(
				# 	">>>>>>>>>>>> _update_clip_notes called, tuple")
				# self._control_surface.log_message(
				# 	"clip notes about to be written"
				# )
				clip.replace_selected_notes(tuple())
			else:
				# debug
				# self._control_surface.log_message(
				# 	">>>>>>>>>>>>>>>> _update_clip_notes called, cache size=%d" %
				# 	len(self._note_cache))
				# self._control_surface.log_message(
				# 	"clip notes about to be written"
				# )
				clip.replace_selected_notes(note_cache)
				
	def update(self, force=False):
		if not self._initializing:
			self._control_surface.log_message(
				"UPDATE CALLED mode=%s" % self._mode
			)
		if force:
			self._force_update = True
		if self.is_enabled():
			self._update_mode_notes_octaves_button()
			self._update_mode_notes_lengths_button()
			self._update_mode_notes_velocities_button()
			#self._update_mode_zoom_button()
			#self._update_mode_notes_pitches_button()
			self._update_clip_toggle_button()
			self._update_matrix()

	def request_display_page(self):
		pass

	def set_height(self, height):
		pass

# MATRIX

	def set_matrix(self, matrix):
		assert isinstance(matrix, (ButtonMatrixElement, type(None)))
		self._grid_buffer = [
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0]
		]
		self._grid_back_buffer = [
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
		 	[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0],
			[0, 0, 0, 0, 0, 0, 0, 0]
		]

		# remove old listeners
		if self._matrix != None:
			for x in range(8):
				for y in range(7):
					button = self._matrix.get_button(x, y)
					button.remove_value_listener(self._matrix_value)

		# assign FIRST
		self._matrix = matrix

		# add listeners to NEW matrix
		if self._matrix != None:
			for x in range(8):
				for y in range(7):
					button = self._matrix.get_button(x, y)
					button.add_value_listener(
						self._matrix_value,
						identify_sender=True
					)

		self._force_update = True

		# debug
		# self._control_surface.log_message("MATRIX ASSIGNED")

	def _update_matrix(self):  # step grid LEDs are updated here
		# self._control_surface.log_message(
		# 	"UPDATE MATRIX mode=%s vel_wait=%s len_wait=%s edit=%s"
		# 	% (
		# 		self._mode,
		# 		self._velocity_wait_animation,
		# 		self._length_wait_animation,
		# 		self._editing_step
		# 	)
		# )

		# Check if we are in an animation state to prevent buffer clearing
		in_animation = self._velocity_wait_animation or self._length_wait_animation

		# Define effective_page at the start - needed for both animation and normal rendering
		effective_page = self._get_effective_page()

		# WAITING ANIMATION: Velocity
		if self._velocity_wait_animation:
			now = time.time()
			for x in range(8):
				delay = self._velocity_wait_start_times[x]

				# Calculate elapsed time since the specific column's start
				elapsed_col = now - delay

				# If this is the very first run or timer is in future, force "Waiting" state
				if elapsed_col <= 0.0:
					# Clear column only
					for y in range(7):
						self._grid_back_buffer[x][y] = 0
					continue

				# --- CONFIGURATION ---
				SPAWN_COEFFICIENT = 2.5  # Tune this: Lower = faster spawns
				FALL_SPEED = 12.0

				time_to_fall = 7.0 / FALL_SPEED
				base_wait = (x * 0.37) % 1.0  # 0 to 1 second base randomness
				random_wait = base_wait * SPAWN_COEFFICIENT  # Scale by coefficient

				cycle_duration = time_to_fall + random_wait

				# Handle potential float precision issues
				time_in_cycle = elapsed_col % cycle_duration

				head_pos = -1  # Default to nothing visible

				# Only draw if we are in the "falling" phase of the cycle
				if time_in_cycle < time_to_fall:
					pos_float = (time_in_cycle / time_to_fall) * 7.0
					head_pos = int(pos_float)
					if head_pos > 6: head_pos = 6

				# Clear Column (Always Black first)
				for y in range(7):
					self._grid_back_buffer[x][y] = 0

				if head_pos != -1:
					# Draw Bright Head
					self._grid_back_buffer[x][head_pos] = "StepSequencer2.Velocity.On"
					# Draw Dimmed Trail
					for t in range(1, 3):
						trail_pos = head_pos - t
						if trail_pos >= 0:
							self._grid_back_buffer[x][trail_pos] = "StepSequencer2.Velocity.Dim"

		# WAITING ANIMATION: Length
		if self._length_wait_animation:
			now = time.time()
			for x in range(8):
				delay = self._length_wait_start_times[x]
				elapsed_col = now - delay

				if elapsed_col <= 0.0:
					for y in range(7):
						self._grid_back_buffer[x][y] = 0
					continue

				# Configuration
				SPAWN_COEFFICIENT = 2.0
				FALL_SPEED = 12.0
				time_to_fall = 7.0 / FALL_SPEED
				base_wait = ((x + 1) * 0.43) % 1.0
				random_wait = base_wait * SPAWN_COEFFICIENT  # Scaled by coefficient

				cycle_duration = time_to_fall + random_wait
				time_in_cycle = elapsed_col % cycle_duration

				head_pos = -1
				if time_in_cycle < time_to_fall:
					pos_float = (time_in_cycle / time_to_fall) * 7.0
					head_pos = int(pos_float)
					if head_pos > 6: head_pos = 6

				for y in range(7):
					self._grid_back_buffer[x][y] = 0

				if head_pos != -1:
					self._grid_back_buffer[x][head_pos] = "StepSequencer2.Length.On"
					for t in range(1, 3):
						trail_pos = head_pos - t
						if trail_pos >= 0:
							self._grid_back_buffer[x][trail_pos] = "StepSequencer2.Length.Dim"

		# If NOT in animation mode, proceed with normal rendering logic
		if not in_animation:
			if self.is_enabled() and self._matrix != None:
				# clear back buffer
				for x in range(8):
					for y in range(7):
						self._grid_back_buffer[x][y] = 0

				# update back buffer
				if self._clip != None:

					for x in range(8):
						has_note = False
						idx = self._get_step_index(x)
						# detect if column has any note
						for y in range(7):
							if self._notes_pitches[(idx) * 7 + 6 - y] == 1:
								has_note = True

						for y in range(7):

							if self._mode == STEPSEQ_MODE_NOTES:
								if self._notes_pitches[(idx) * 7 + 6 - y] == 1:
									self._grid_back_buffer[x][y] = "StepSequencer2.Pitch.On"
								else:
									self._grid_back_buffer[x][y] = "StepSequencer2.Pitch.Off"

							elif self._mode == STEPSEQ_MODE_NOTES_OCTAVES:
								if has_note:
									if self._notes_octaves[idx] == 6 - y:
										self._grid_back_buffer[x][y] = "StepSequencer2.Octave.On"
									else:
										self._grid_back_buffer[x][y] = "StepSequencer2.Octave.Off"
								else:
									if self._notes_octaves[idx] == 6 - y:
										self._grid_back_buffer[x][y] = "StepSequencer2.Octave.Dim"
									else:
										self._grid_back_buffer[x][y] = "StepSequencer2.Octave.Off"

							elif self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR:
								self._control_surface.log_message("DRAWING VELOCITY EDITOR")
								if self._editing_step is None:
									return
								# notes = self._get_notes_at_step(self._editing_step)
								for y in range(7):
									pitch = self._pitch_for_row(y)
									note = self._get_note_for_pitch_at_step(self._editing_step, pitch)

									if note is None:
										default_velocity = 90
										bucket = 0

										for i, v in enumerate(self._velocity_map):
											if default_velocity >= v:
												bucket = i

										for col in range(8):
											if col <= bucket:
												self._grid_back_buffer[col][y] = \
													"StepSequencer2.Velocity.Dim"
											else:
												self._grid_back_buffer[col][y] = \
													"StepSequencer2.Velocity.Off"

										continue
									velocity = note[3]
									bucket = 0

									for i, v in enumerate(self._velocity_map):
										if velocity >= v:
											bucket = i

									for col in range(8):
										if col <= bucket:
											self._grid_back_buffer[col][y] = "StepSequencer2.Velocity.On"
										else:
											self._grid_back_buffer[col][y] = "StepSequencer2.Velocity.Off"

							elif self._mode == STEPSEQ_MODE_VERTICAL_VELOCITY:
								# VERTICAL VELOCITY DISPLAY
								# Each column shows the MAX velocity of any note in that step as a vertical bar.
								# RED COLOR if notes in the step have DIFFERENT velocities

								for col_x in range(8):
									idx = self._get_step_index(col_x)
									step_notes = self._get_notes_at_step(idx)

									max_vel_idx = 0  # Default: Bottom-most (lowest velocity)
									has_mixed_velocities = False

									if step_notes:
										# Collect all velocity buckets for this step
										velocity_buckets = []
										for note in step_notes:
											note_vel = note[3]  # Index 3 is Velocity!
											bucket = 0
											for i, v in enumerate(self._velocity_map):
												if note_vel >= v:
													bucket = i
											velocity_buckets.append(bucket)

											# Track max velocity for the bar height
											if bucket > max_vel_idx:
												max_vel_idx = bucket

										# Clamp to grid height (7 rows = indices 0-6)
										if max_vel_idx > 6: max_vel_idx = 6

										# CHECK IF VELOCITIES ARE MIXED
										if len(velocity_buckets) > 1:
											# Multiple velocity buckets found in this step
											first_bucket = velocity_buckets[0]
											for b in velocity_buckets[1:]:
												if b != first_bucket:
													has_mixed_velocities = True
													break

									# Draw the vertical bar with appropriate color
									for row_y in range(7):
										row_bucket = 6 - row_y

										if has_mixed_velocities and row_bucket <= max_vel_idx:
											# USE RED COLOR FOR MIXED VELOCITIES
											self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Velocity.Mixed"
										elif row_bucket <= max_vel_idx:
											# Normal ON color
											self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Velocity.On"
										else:
											# Normal DIM color
											self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Velocity.Dim"

							elif self._mode == STEPSEQ_MODE_VERTICAL_LENGTH:
								# VERTICAL VELOCITY MODE: Each column has ONE length value
								# Displayed as a vertical bar in that column

								for x in range(8):
									idx = self._get_step_index(x)

									# Get the average/max length for this step/column
									# We'll use the first note found in this step as representative
									step_notes = self._get_notes_at_step(idx)
									max_vel_idx = 0  # Default low length bucket

									if step_notes:
										# Find highest length in this step
										for note in step_notes:
											note_vel = note[3]
											vel_bucket = 0
											for i, v in enumerate(self._length_map):
												if note_vel >= v:
													vel_bucket = i
											if vel_bucket > max_vel_idx:
												max_vel_idx = vel_bucket

									# OR use average length logic if preferred
									# avg_vel = sum(n[3] for n in step_notes) / len(step_notes)
									# ... convert to bucket ...

									# Draw vertical bar: rows from bottom (6) up to max_vel_idx
									for y in range(7):
										# Row 0 = Top (High Pitch), Row 6 = Bottom (Low Pitch)
										# We want the bar to grow from bottom up
										grid_row = 6 - y  # Invert: 6=bottom, 0=top

										if grid_row <= max_vel_idx:
											self._grid_back_buffer[x][y] = "StepSequencer2.Velocity.On"
										else:
											self._grid_back_buffer[x][y] = "StepSequencer2.Velocity.Dim"

							elif self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
								if self._editing_step is None:
									return
								# notes = self._get_notes_at_step(self._editing_step)
								for y in range(7):
									pitch = self._pitch_for_row(y)
									note = self._get_note_for_pitch_at_step(self._editing_step, pitch)

									if note is None:
										continue
									length = note[2]  # Note: length is index 2, not 3
									bucket = 0

									for i, v in enumerate(self._length_map):
										if length >= v:
											bucket = i

									for col in range(8):
										if col <= bucket:
											self._grid_back_buffer[col][y] = "StepSequencer2.Length.On"
										else:
											self._grid_back_buffer[col][y] = "StepSequencer2.Length.Off"

				# --- METRONOME ---
				if self._playhead != None:
					play_position = int(self._playhead / self.quantization)
					play_x_position = play_position % 8
					page = int(play_position / 8)

					if page == effective_page:

						metronome_color = "StepSequencer2.NoteEditor.MetronomeInPage"

						# full column metronome
						for y in range(7):
							self._grid_back_buffer[play_x_position][y] = metronome_color

						# playing notes (ONLY current visible page)
						if self._mode == STEPSEQ_MODE_NOTES:
							for y in range(7):
								if self._notes_pitches[play_position * 7 + 6 - y] == 1:
									self._grid_back_buffer[play_x_position][y] = "StepSequencer2.NoteEditor.PlayInPage"

			else:
				for x in range(8):
					for y in range(7):
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"

		# --- PUSH TO HARDWARE (cache optimization) ---
		# This block runs regardless of whether we are animating or doing normal rendering
		if self._matrix != None:
			for x in range(8):
				for y in range(7):
					if self._grid_back_buffer[x][y] != self._grid_buffer[x][y] or self._force_update:
						self._grid_buffer[x][y] = self._grid_back_buffer[x][y]
						self._matrix.get_button(x, y).set_light(self._grid_buffer[x][y])

			self._force_update = False

	def _matrix_value(self, value, sender):  # matrix buttons listener
		# Identify x and y coordinates from the sender
		x = -1
		y = -1
		for xx in range(8):
			for yy in range(7):
				if self._matrix.get_button(xx, yy) == sender:
					x = xx
					y = yy

		# Skip if we're in velocity/length editor but pressed row 7 (reserved for loop controls)
		if self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR or self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
			if y == 7:
				return

		effective_page = self._get_effective_page()

		if self.is_enabled() and self._matrix != None:
			if self._clip == None:
				self._step_sequencer.create_clip()
			else:
				start = int(self._clip.loop_start / self._resolution)
				end = int(self._clip.loop_end / self._resolution)

				if (effective_page + 1) * 8 > end or effective_page * 8 < start:
					# Current page is outside of running loop, only update this page
					start = effective_page * 8
					end = (effective_page + 1) * 8

				# --- CRITICAL FIX: HANDLE ANIMATION INTERRUPTION ---
				# If we are currently animating (waiting for user selection),
				# pressing a grid button stops the animation and enters Horizontal Editor immediately.
				if self._velocity_wait_animation and ((value != 0) or (not sender.is_momentary())) and y < 7:
					idx = self._get_step_index(x)

					# Stop Animation
					self._velocity_wait_animation = False
					self._mode_notes_velocities_button.turn_off()  # Ensure button LED turns off

					# Set up Horizontal Editor state
					self._pending_velocity_editor = False
					self._editing_step = idx
					self.set_mode(STEPSEQ_MODE_STEP_VELOCITY_EDITOR)

					self._control_surface.show_message("Horz Vel Step %d" % idx)
					self._update_matrix()
					return  # Exit function to prevent further processing

				# --- EXISTING PENDING LOGIC (For Length Editor mostly) ---
				if self._pending_velocity_editor:
					# This block is now primarily a fallback if animation logic was bypassed
					idx = self._get_step_index(x)
					self._editing_step = idx

					self._control_surface.log_message(
						"ENTERING VELOCITY EDITOR step=%s" % idx
					)
					self._velocity_wait_animation = False
					self._pending_velocity_editor = False
					self.set_mode(STEPSEQ_MODE_STEP_VELOCITY_EDITOR)
					return

				if self._pending_length_editor:
					idx = self._get_step_index(x)
					self._editing_step = idx
					self._pending_length_editor = False
					self.set_mode(STEPSEQ_MODE_STEP_LENGTH_EDITOR)
					return

				# --- MAIN BUTTON PRESS HANDLING ---
				if ((value != 0) or (not sender.is_momentary())) and y < 7:
					idx = self._get_step_index(x)

					if self._mode == STEPSEQ_MODE_NOTES:
						# Toggle note at grid position
						if self._notes_pitches[(idx) * 7 + 6 - y] == 1:
							self._notes_pitches[(idx) * 7 + 6 - y] = 0
						else:
							# Clear step if monophonic mode
							if self._is_monophonic:
								for yy in range(7):
									self._notes_pitches[(idx) * 7 + 6 - yy] = 0
							self._notes_pitches[(idx) * 7 + 6 - y] = 1

					elif self._mode == STEPSEQ_MODE_NOTES_OCTAVES:
						if self._is_notes_octaves_shifted:
							if x < 4:
								for x1 in range(start, end):
									self._notes_octaves[x1] = 6 - y
							else:
								for x1 in range(start, end):
									if y < 3 and self._notes_octaves[x1] < 6:
										self._notes_octaves[x1] = self._notes_octaves[x1] + 1
									if y > 3 and self._notes_octaves[x1] > 0:
										self._notes_octaves[x1] = self._notes_octaves[x1] - 1
						else:
							self._notes_octaves[idx] = 6 - y

					elif self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR:
						# HORIZONTAL VELOCITY MODE
						pitch = self._pitch_for_row(y)
						velocity = self._velocity_map[x]
						note = self._get_note_for_pitch_at_step(self._editing_step, pitch)

						if note is None:
							self._add_note_at_step(self._editing_step, pitch, velocity)
						else:
							self._set_velocity_for_pitch_at_step(self._editing_step, pitch, velocity)

						self._update_matrix()
						return

					elif self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
						# LENGTH MODE
						pitch = self._pitch_for_row(y)
						length = self._length_map[x]
						note = self._get_note_for_pitch_at_step(self._editing_step, pitch)

						if note is None:
							self._add_note_at_step(self._editing_step, pitch, length)
						else:
							self._set_length_for_pitch_at_step(self._editing_step, pitch, length)

						self._update_matrix()
						return

					elif self._mode == STEPSEQ_MODE_VERTICAL_VELOCITY:
						# VERTICAL VELOCITY MODE
						# Set velocity for ALL notes in column x based on pressed row y
						vel_bucket = 6 - y
						if vel_bucket < 0: vel_bucket = 0
						if vel_bucket > 7: vel_bucket = 7

						target_velocity = self._velocity_map[vel_bucket]

						step_notes = list(self._note_cache)
						start_time = idx * self._resolution
						end_time = start_time + self._resolution
						changed = False

						for i, note in enumerate(step_notes):
							pitch, time, length, old_vel, muted = note
							if start_time <= time < end_time:
								if old_vel != target_velocity:
									step_notes[i] = (pitch, time, length, target_velocity, muted)
									changed = True

						if changed:
							self._write_note_cache_to_clip(step_notes)
							self._update_matrix()

						return

					# Default handling for other modes (Copy/Paste, etc.)
					self._update_matrix()

					if self._mode == STEPSEQ_MODE_NOTES:
						self._toggle_note_at_grid_position(idx, y)
						return
					else:
						self._update_clip_notes()

	def _add_note_at_step(self, idx, pitch, velocity):
		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		notes = list(self._note_cache)

		# Don't create duplicates
		for note in notes:
			if note[0] == pitch and start_time <= note[1] < end_time:
				return

		notes.append(
			(pitch, start_time, self._resolution, velocity, False)
		)

		self._write_note_cache_to_clip(notes)
		self._note_cache = tuple(notes)

	def _pitch_for_row(self, y):
		return (self._key_indexes[6 - y] + 12 * (self._display_octave - 2))

	def _get_step_index(self, x):
		return x + 8 * self._get_effective_page()

	def _set_velocity_for_pitch_at_step(self, idx, pitch, velocity):
		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		notes = list(self._note_cache)

		changed = False

		for i, note in enumerate(notes):

			note_pitch, time, length, old_velocity, muted = note

			if (note_pitch == pitch and start_time <= time < end_time):
				notes[i] = (note_pitch, time, length, velocity, muted)
				changed = True

		if changed:
			self._write_note_cache_to_clip(notes)


	def _set_length_for_pitch_at_step(self, idx, pitch, length):
		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		notes = list(self._note_cache)

		changed = False

		for i, note in enumerate(notes):

			note_pitch, time, length, old_length, muted = note

			if (note_pitch == pitch and start_time <= time < end_time):
				notes[i] = (note_pitch, time, length, length, muted)
				changed = True

		if changed:
			self._write_note_cache_to_clip(notes)

	def _velocity_wait_tick(self):
		self._control_surface.log_message(
			"VELOCITY TICK anim=%s" %
			self._velocity_wait_animation
		)
		if not self._velocity_wait_animation:
			self._control_surface.log_message(
				"VELOCITY ANIMATION FINISHED"
			)
			return
		self._update_matrix()
		self._control_surface.schedule_message(1,self._velocity_wait_tick)

	def _length_wait_tick(self):
		if not self._length_wait_animation:
			return
		self._update_matrix()
		self._control_surface.schedule_message(1,self._length_wait_tick)

# LOOP SELECTOR
	def _get_effective_page(self):
		return self._page


# CLIP ON/OFF
	def _update_clip_toggle_button(self):

		if not self.is_enabled():
			return

		if self._clip_toggle_button is None:
			return

		clip_slot = self.song().view.highlighted_clip_slot

		if clip_slot is None or not clip_slot.has_clip:
			self._clip_toggle_button.set_light(
				"DefaultButton.Disabled"
			)
			return

		clip = clip_slot.clip

		# debug
		# self._control_surface.log_message(
		# 	"playing=%s triggered=%s slot_playing=%s" %
		# 	(
		# 		str(clip.is_playing),
		# 		str(clip_slot.is_triggered),
		# 		str(clip_slot.is_playing)
		# 	)
		# )

		self._clip_toggle_button.set_on_off_values(
			"StepSequencer2.Clip.On",
			"StepSequencer2.Clip.Off"
		)

		# BLINKING
		if clip_slot.is_triggered:
			self._clip_toggle_button.set_light(
				"StepSequencer2.Clip.Triggered"
			)

		# PLAYING
		elif clip_slot.is_playing:
			self._clip_toggle_button.turn_on()

		# STOPPED
		else:
			self._clip_toggle_button.turn_off()

	def set_clip_toggle_button(self, button):
		# debug
		#self._control_surface.log_message(">>>>>>>>>> set_clip_toggle_button called")
		assert (isinstance(button, (ButtonElement, type(None))))

		if (self._clip_toggle_button != button):

			if (self._clip_toggle_button != None):
				self._clip_toggle_button.remove_value_listener(
					self._clip_toggle_button_value
				)

			self._clip_toggle_button = button

			if (self._clip_toggle_button != None):

				assert isinstance(button, ButtonElement)

				self._clip_toggle_button.add_value_listener(
					self._clip_toggle_button_value,
					identify_sender=True
				)

	def _clip_toggle_button_value(self, value, sender):

		assert (self._clip_toggle_button != None)
		assert (value in range(128))

		#if self.is_enabled() and self._clip != None:
		clip_slot = self.song().view.highlighted_clip_slot
		#if self.is_enabled() and self._clip != None:
		if (self.is_enabled() and clip_slot is not None and clip_slot.has_clip):

			if ((value != 0) or (not sender.is_momentary())):

				clip_slot = self.song().view.highlighted_clip_slot

				if clip_slot != None:

					# toggle playback
					if clip_slot.is_playing:
						clip_slot.stop()
						self._control_surface.show_message("clip stopped")
					else:
						clip_slot.fire()
						self._control_surface.show_message("clip playing")

					self._control_surface.schedule_message(1,self._update_clip_toggle_button)

	def _remove_highlighted_clip_slot_listener(self):
		try:
			self.song().view.remove_selected_track_listener(self._on_selected_track_changed)
		except:
			pass

		try:
			self.song().view.remove_selected_scene_listener(self._on_selected_scene_changed)
		except:
			pass

	def _on_selected_track_changed(self):
		self._on_clip_slot_changed()

	def _on_selected_scene_changed(self):
		self._on_clip_slot_changed()

	def _on_clip_slot_changed(self):
		self._register_clip_slot_listener()
		self._update_clip_toggle_button()

	def _remove_clip_slot_listener(self):

		if self._clip_slot != None:

			try:
				if self._clip_slot.playing_status_has_listener(
						self._on_clip_playing_changed):
					self._clip_slot.remove_playing_status_listener(
						self._on_clip_playing_changed)
			except:
				pass

			try:
				if self._clip_slot.is_triggered_has_listener(
						self._on_clip_triggered_changed):
					self._clip_slot.remove_is_triggered_listener(
						self._on_clip_triggered_changed)
			except:
				pass

		# REMOVE CLIP LISTENER
		if self._clip != None:

			try:
				if self._clip.playing_status_has_listener(
						self._on_clip_playing_changed):
					self._clip.remove_playing_status_listener(
						self._on_clip_playing_changed)
			except:
				pass

	def _register_clip_slot_listener(self):

		self._remove_clip_slot_listener()

		self._clip_slot = self.song().view.highlighted_clip_slot

		if self._clip_slot != None:

			# SLOT PLAYING
			try:
				if not self._clip_slot.playing_status_has_listener(
						self._on_clip_playing_changed):
					self._clip_slot.add_playing_status_listener(
						self._on_clip_playing_changed)
			except:
				pass

			# SLOT TRIGGERED
			try:
				if not self._clip_slot.is_triggered_has_listener(
						self._on_clip_triggered_changed):
					self._clip_slot.add_is_triggered_listener(
						self._on_clip_triggered_changed)
			except:
				pass

			# CLIP PLAYING
			if self._clip_slot.has_clip:

				# self._clip = self._clip_slot.clip --> removed because it otherwise empties top slot's clip.

				try:
					if not self._clip.playing_status_has_listener(
							self._on_clip_playing_changed):
						self._clip.add_playing_status_listener(
							self._on_clip_playing_changed)
				except:
					pass

		self._update_clip_toggle_button()

	def _on_clip_playing_changed(self):
		self._update_clip_toggle_button()

	def _on_clip_triggered_changed(self):
		self._update_clip_toggle_button()

# ZOOM --> controlled by StepSequencerComponent()
# 	def _update_mode_zoom_button(self):
# 		if self.is_enabled():
# 			if (self._mode_zoom_button != None):
# 				if self._clip != None:
# 					self._mode_zoom_button.set_on_off_values("StepSequencer2.Zoom.On", "StepSequencer2.Zoom.Dim")
# 					if self._mode == STEPSEQ_MODE_NOTES:
# 						self._mode_zoom_button.turn_on()
# 					else:
# 						self._mode_zoom_button.turn_off()
# 				else:
# 					self._mode_zoom_button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")
# 					self._mode_zoom_button.turn_off()
#
# 	def set_mode_zoom_button(self, button):
# 		assert (isinstance(button, (ButtonElement, type(None))))
# 		if (self._mode_zoom_button != button):
# 			if (self._mode_zoom_button != None):
# 				self._mode_zoom_button.remove_value_listener(self._mode_button_zoom_value)
# 			self._mode_zoom_button = button
# 			if (self._mode_zoom_button != None):
# 				assert isinstance(button, ButtonElement)
# 				self._mode_zoom_button.add_value_listener(self._mode_button_zoom_value, identify_sender=True)
#
# 	def _mode_button_zoom_value(self, value, sender):
# 		assert (self._mode_zoom_button != None)
# 		assert (value in range(128))
# 		if self.is_enabled() and self._clip != None:
# 			if ((value ==0) and (sender.is_momentary())):
# 				self._is_zoom_shifted = False
# 				self._is_mute_shifted = False
# 				self._is_velocity_shifted = False
# 				self.update()
# 			else:
# 				self._is_zoom_shifted = True
# 				self._is_mute_shifted = True
# 				self._is_velocity_shifted = True
# 				self._is_zoom_shifted = True


# PITCHES
	def _update_mode_notes_pitches_button(self):
		if self.is_enabled():
			if (self._mode_notes_pitches_button != None):
				if self._clip != None:
					self._mode_notes_pitches_button.set_on_off_values("StepSequencer2.Pitch.On", "StepSequencer2.Pitch.Dim")
					if self._mode == STEPSEQ_MODE_NOTES:
						self._mode_notes_pitches_button.turn_on()
					else:
						self._mode_notes_pitches_button.turn_off()
				else:
					self._mode_notes_pitches_button.set_on_off_values("DefaultButton.Disabled", "DefaultButton.Disabled")
					self._mode_notes_pitches_button.turn_off()

	def set_mode_notes_pitches_button(self, button):
		assert (isinstance(button, (ButtonElement, type(None))))
		if (self._mode_notes_pitches_button != button):
			if (self._mode_notes_pitches_button != None):
				self._mode_notes_pitches_button.remove_value_listener(self._mode_button_notes_pitches_value)
			self._mode_notes_pitches_button = button
			if (self._mode_notes_pitches_button != None):
				assert isinstance(button, ButtonElement)
				self._mode_notes_pitches_button.add_value_listener(self._mode_button_notes_pitches_value, identify_sender=True)

	def _mode_button_notes_pitches_value(self, value, sender):
		assert (self._mode_notes_pitches_button != None)
		assert (value in range(128))
		if self.is_enabled() and self._clip != None:
			if ((value ==0) and (sender.is_momentary())):
				#self._is_notes_pitches_shifted = False
				self._is_mute_shifted = False
				self._is_velocity_shifted = False
				if time.time() - self._last_notes_pitches_button_press < 0.500:
					self._is_monophonic = not self._is_monophonic
					self._update_clip_notes()
					self._step_sequencer._update_OSD()
				else:
					self.set_mode(STEPSEQ_MODE_NOTES)
					self._control_surface.show_message("pitch")
					self.update()
					self._step_sequencer._update_OSD()
				self._last_notes_pitches_button_press = time.time()
			else:
				#self._is_notes_pitches_shifted = True
				self._is_mute_shifted = True
				self._is_velocity_shifted = True


# OCTAVES
	# debug
	def set_display_octave(self, octave):
		self._display_octave = max(0, min(6, octave))

		self._control_surface.log_message(
			"DISPLAY OCTAVE = %s" % self._display_octave
		)

		self._parse_notes()
		self._force_update = True
		self.update()

	def _update_mode_notes_octaves_button(self):
		if self.is_enabled():
			if (self._mode_notes_octaves_button != None):
				if self._clip != None:
					self._mode_notes_octaves_button.set_on_off_values("StepSequencer2.Octave.On", "StepSequencer2.Octave.Dim")
					if self._mode == STEPSEQ_MODE_NOTES_OCTAVES:
						self._mode_notes_octaves_button.turn_on()
					else:
						self._mode_notes_octaves_button.turn_off()
				else:
					self._mode_notes_octaves_button.set_light("DefaultButton.Disabled")

	def set_mode_notes_octaves_button(self, button):
		assert (isinstance(button, (ButtonElement, type(None))))
		if (self._mode_notes_octaves_button != button):
			if (self._mode_notes_octaves_button != None):
				self._mode_notes_octaves_button.remove_value_listener(self._mode_button_notes_octaves_value)
			self._mode_notes_octaves_button = button
			if (self._mode_notes_octaves_button != None):
				assert isinstance(button, ButtonElement)
				self._mode_notes_octaves_button.add_value_listener(self._mode_button_notes_octaves_value, identify_sender=True)

	def _mode_button_notes_octaves_value(self, value, sender):
		assert (self._mode_notes_octaves_button != None)
		assert (value in range(128))
		if self.is_enabled() and self._clip != None:
			if ((value ==0) and (sender.is_momentary())):
				self._is_notes_octaves_shifted = False
				self.set_mode(STEPSEQ_MODE_NOTES_OCTAVES)
				self._control_surface.show_message("octave")
				self.update()
				self._step_sequencer._update_OSD()
			else:
				self._is_notes_octaves_shifted = True

# VELOCITIES
	def _set_velocity_at_step(self, idx, velocity_index):

		velocity = self._velocity_map[velocity_index]

		notes = list(self._note_cache)

		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		changed = False

		for i, note in enumerate(notes):

			pitch, time, length, old_velocity, muted = note

			if start_time <= time < end_time:
				notes[i] = (
					pitch,
					time,
					length,
					velocity,
					muted
				)

				changed = True

		if changed:
			self._write_note_cache_to_clip(notes)


	def _update_mode_notes_velocities_button(self):
		if not self.is_enabled():
			return

		if self.is_enabled():
			if (self._mode_notes_velocities_button != None):
				if self._clip != None:
					self._mode_notes_velocities_button.set_on_off_values("StepSequencer2.Velocity.On",
					                                                     "StepSequencer2.Velocity.Dim")
					# Only turn ON if currently ANIMATING.
					# In Horizontal/Vertical modes, it should be OFF (acting as an exit button).
					if self._velocity_wait_animation:
						self._mode_notes_velocities_button.turn_on()
					else:
						self._mode_notes_velocities_button.turn_off()
				else:
					self._mode_notes_velocities_button.set_light("DefaultButton.Disabled")

	def set_mode_notes_velocities_button(self, button):
		assert (isinstance(button, (ButtonElement, type(None))))
		if (self._mode_notes_velocities_button != button):
			if (self._mode_notes_velocities_button != None):
				self._mode_notes_velocities_button.remove_value_listener(self._mode_button_notes_velocities_value)
			self._mode_notes_velocities_button = button
			if (self._mode_notes_velocities_button != None):
				assert isinstance(button, ButtonElement)
				self._mode_notes_velocities_button.add_value_listener(self._mode_button_notes_velocities_value, identify_sender=True)

	def _mode_button_notes_velocities_value(self, value, sender):
		assert (self._mode_notes_velocities_button != None)
		assert (value in range(128))

		if self.is_enabled() and self._clip != None:
			if ((value == 0) and (sender.is_momentary())):
				self._is_mute_shifted = False
				self._is_notes_velocities_shifted = False

				# STATE 1: Currently in Horizontal Velocity Editor -> Exit to Notes
				if self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR:
					self._velocity_wait_animation = False
					self._pending_velocity_editor = False
					self._editing_step = None
					self._is_velocity_editor_vertical = False
					self.set_mode(STEPSEQ_MODE_NOTES)
					self._control_surface.show_message("pitch")
					return

				# STATE 2: Currently in Vertical Velocity Editor -> Exit to Notes
				elif self._mode == STEPSEQ_MODE_VERTICAL_VELOCITY:
					self._velocity_wait_animation = False
					self._pending_velocity_editor = False
					self._editing_step = None
					self._is_velocity_editor_vertical = False
					self.set_mode(STEPSEQ_MODE_NOTES)
					self._control_surface.show_message("pitch")
					return

				# STATE 3: Currently in Animation Mode -> Enter Vertical Mode
				elif self._velocity_wait_animation:
					# Stop animation immediately
					self._velocity_wait_animation = False
					# Cancel any pending horizontal entry
					self._pending_velocity_editor = False
					# Switch to Vertical Mode
					self.set_mode(STEPSEQ_MODE_VERTICAL_VELOCITY)
					self._control_surface.show_message("vert vel")
					self.update()
					self._step_sequencer._update_OSD()
					return

				# STATE 4: In Normal Notes Mode -> Enter Animation Mode
				else:
					# Reset any previous editor states
					self._pending_velocity_editor = False
					self._editing_step = None
					self._is_velocity_editor_vertical = False

					# Start Animation
					self._velocity_wait_animation = True
					now = time.time()
					# First column starts immediately
					self._velocity_wait_start_times[0] = now
					for x in range(1,8):
						# CHANGE THIS LINE: Reduce the delay range from (0.5, 2.5) to (0.0, 0.3)
						# This makes the first pad appear immediately (<0.3s) instead of waiting up to 2.5s
						self._velocity_wait_start_times[x] = now + uniform(0.0, 0.3)

					# Force update to show animation immediately
					self._force_update = True

					# Schedule the animation loop
					self._control_surface.schedule_message(1, self._velocity_wait_tick)

					self._control_surface.show_message("Select step or press Vel again")

				# Update button LED state
				if self._velocity_wait_animation:
					self._mode_notes_velocities_button.turn_on()
				else:
					self._mode_notes_velocities_button.turn_off()

			else:
				self._is_mute_shifted = True
				self._is_notes_velocities_shifted = True

			self._step_sequencer._is_mute_shifted = self._is_mute_shifted

		# Always call update to refresh the matrix
		self.update()
		self._step_sequencer._update_OSD()

# LENGTHS
	def _set_length_at_step(self, idx, length_index):

		length = (
				self._length_map[length_index]
				* self._resolution
				/ 4.0
		)

		notes = list(self._note_cache)

		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		changed = False

		for i, note in enumerate(notes):

			pitch, time, old_length, velocity, muted = note

			if start_time <= time < end_time:
				notes[i] = (
					pitch,
					time,
					length,
					velocity,
					muted
				)

				changed = True

		if changed:
			self._write_note_cache_to_clip(notes)

	def _update_mode_notes_lengths_button(self):

		if not self.is_enabled():
			return

		if self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR:
			self._mode_notes_velocities_button.turn_on()
		else:
			self._mode_notes_velocities_button.turn_off()

		if self.is_enabled():
			if (self._mode_notes_lengths_button != None):
				if self._clip != None:
					self._mode_notes_lengths_button.set_on_off_values("StepSequencer2.Length.On", "StepSequencer2.Length.Dim")
					if self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:#STEPSEQ_MODE_NOTES_LENGTHS:
						self._mode_notes_lengths_button.turn_on()
					else:
						self._mode_notes_lengths_button.turn_off()
				else:
					self._mode_notes_lengths_button.set_light("DefaultButton.Disabled")
			

	def set_mode_notes_lengths_button(self, button):
		assert (isinstance(button, (ButtonElement, type(None))))
		if (self._mode_notes_lengths_button != button):
			if (self._mode_notes_lengths_button != None):
				self._mode_notes_lengths_button.remove_value_listener(self._mode_button_notes_lengths_value)
			self._mode_notes_lengths_button = button
			if (self._mode_notes_lengths_button != None):
				assert isinstance(button, ButtonElement)
				self._mode_notes_lengths_button.add_value_listener(self._mode_button_notes_lengths_value, identify_sender=True)

	def _mode_button_notes_lengths_value(self, value, sender):
		assert (self._mode_notes_lengths_button != None)
		assert (value in range(128))
		if not self._pending_length_editor:
			self._pending_length_editor = True
			self._control_surface.show_message("Select step for length edit")
			return

		if self.is_enabled() and self._clip != None:

			if ((value == 0) and (sender.is_momentary())):

				self._is_notes_lengths_shifted = False

				# TOGGLE BETWEEN LENGTHS <-> NOTES
				if self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:#STEPSEQ_MODE_NOTES_LENGTHS:
					self._editing_step = None
					self.set_mode(STEPSEQ_MODE_NOTES)
					self._control_surface.show_message("pitch")
					return
				else:
					self._pending_length_editor = True
					self._length_wait_animation = True
					self._length_wait_tick()
					self._force_update = True
					now = time.time()

					for x in range(8):
						self._length_wait_start_times[x] = now + uniform(0, 2.5)
					self._control_surface.show_message("Select step for length edit")

				self.update()
				self._step_sequencer._update_OSD()

			else:
				self._is_notes_lengths_shifted = True




	# COPY / PASTE
	def _update_mode_copy_paste_button(self):
		if self.is_enabled():
			if (self._mode_copy_paste_button != None):
				if self._clip != None:
					self._mode_copy_paste_button.set_on_off_values("StepSequencer2.CopyPaste.On",
					                                                  "StepSequencer2.CopyPaste.Dim")
					if self._mode == STEPSEQ_MODE_COPY_PASTE:
						self._mode_copy_paste_button.turn_on()
					else:
						self._mode_copy_paste_button.turn_off()
				else:
					self._mode_copy_paste_button.set_light("DefaultButton.Disabled")

	def set_mode_copy_paste_button(self, button):
		assert (isinstance(button, (ButtonElement, type(None))))
		if (self._mode_copy_paste_button != button):
			if (self._mode_copy_paste_button != None):
				self._mode_copy_paste_button.remove_value_listener(self._mode_button_copy_paste_value)
			self._mode_copy_paste_button = button
			if (self._mode_copy_paste_button != None):
				assert isinstance(button, ButtonElement)
				self._mode_copy_paste_button.add_value_listener(self._mode_button_copy_paste_value,
				                                                   identify_sender=True)

	def _mode_button_copy_paste_value(self, value, sender):
		assert (self._mode_copy_paste_button != None)
		assert (value in range(128))

		if self.is_enabled() and self._clip != None:

			if ((value == 0) and (sender.is_momentary())):

				self._is_copy_paste_shifted = False
				if self._mode == STEPSEQ_MODE_COPY_PASTE:
					self._control_surface.show_message("Page pasted")
				else:
					self.set_mode(STEPSEQ_MODE_COPY_PASTE)
					self._control_surface.show_message("Page Copied")

				self.update()
				self._step_sequencer._update_OSD()

			else:
				self._is_copy_paste_shifted = True


class StepSequencerComponent2(StepSequencerComponent):

	def __init__(self, matrix, side_buttons, top_buttons, control_surface):
		# Initialization of _loop_page_offset is done in StepSequencerComponent
		self._new_clip_pages = 1
		self._name = "melodic step sequencer"
		super(StepSequencerComponent2, self).__init__(matrix, side_buttons, top_buttons, control_surface)


	def _set_scale_selector(self):
		super(StepSequencerComponent2, self)._set_scale_selector()
		self._scale_selector._mode = "diatonic"
		self._scale_selector._drumrack = False

	def _set_track_controller(self):
		self._track_controller = self.register_component(TrackControllerComponent(self._control_surface, implicit_arm = False))
		self._track_controller.set_prev_scene_button(self._top_buttons[0])
		self._track_controller.set_next_scene_button(self._top_buttons[1])
		self._track_controller.set_prev_track_button(self._top_buttons[2])
		self._track_controller.set_next_track_button(self._top_buttons[3])

	def _set_note_editor(self):
		self._clear_side_button_listeners()
		self._note_editor = self.register_component(MelodicNoteEditorComponent(self, self._matrix, self._side_buttons, self._control_surface))

	def _set_mute_shift_function(self):
		self._is_mute_shifted = False
			
	def _set_note_selector(self):
		self._note_selector = self.register_component(NoteSelectorComponent(self, [], self._control_surface))

	def _set_loop_selector(self):
		self._loop_selector = self.register_component(LoopSelectorComponent(self, [
			self._matrix.get_button(0, 7), self._matrix.get_button(1, 7), self._matrix.get_button(2, 7),
			self._matrix.get_button(3, 7),
			self._matrix.get_button(4, 7), self._matrix.get_button(5, 7), self._matrix.get_button(6, 7),
			self._matrix.get_button(7, 7)
		], self._control_surface))
		self._loop_selector.set_loop_page_offset(self._loop_page_offset)
		#self.set_left_button(self._top_buttons[2])
		#self.set_right_button(self._top_buttons[3])

	def _update_buttons(self):
		self._update_resolution_button()
		#self._update_quantization_button()
		#self._update_lock_button()
		self._update_clip_toggle_button()
		#self._update_cycle_button()
		self._update_scale_selector_button()
		self._update_left_button()
		self._update_right_button()

	def _update_drum_group_device(self):
		# no drum rack mode for me. i am a melodic step seq.
		self._drum_group_device = None

	def _update_OSD(self):
		if self._osd != None:
			self._osd.set_mode('Melodic Step Sequencer')
			if self._clip != None:
				self._osd.attributes[0] = MUSICAL_MODES[self._scale_selector._modus * 2]
				self._osd.attribute_names[0] = "Scale"
				self._osd.attributes[1] = KEY_NAMES[self._scale_selector._key % 12]
				self._osd.attribute_names[1] = "Root Note"
				self._osd.attributes[2] = self._scale_selector._octave
				self._osd.attribute_names[2] = "Octave"
				self._osd.attributes[3] = RESOLUTION_NAMES[self._resolution_index]
				# self._osd.attributes[3] = QUANTIZATION_NAMES[self._quantization_index]
				self._osd.attribute_names[3] = "Quantisation"

				if self._note_editor._is_monophonic:
					self._osd.attributes[4] = "Mono"
				else:
					self._osd.attributes[4] = "Poly"
				self._osd.attribute_names[4] = "Polyphony"

				self._osd.attribute_names[5] = "Mode"  # Changed label from "Page" to "Mode" for clarity

				if self._note_editor._mode == STEPSEQ_MODE_NOTES:
					self._osd.attributes[5] = "Notes"

				elif self._note_editor._mode == STEPSEQ_MODE_NOTES_OCTAVES:
					self._osd.attributes[5] = "Octave"

				elif self._note_editor._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR:
					# Distinguish between Horizontal and Vertical sub-modes
					if hasattr(self._note_editor,
					           '_is_velocity_editor_vertical') and self._note_editor._is_velocity_editor_vertical:
						self._osd.attributes[5] = "Vert Vel"
					else:
						# Default horizontal editor showing the step index
						step_num = self._note_editor._editing_step if self._note_editor._editing_step is not None else -1
						self._osd.attributes[5] = "Horz Step %d" % step_num

				elif self._note_editor._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
					step_num = self._note_editor._editing_step if self._note_editor._editing_step is not None else -1
					self._osd.attributes[5] = "Len Step %d" % step_num

				elif self._note_editor._mode == STEPSEQ_MODE_COPY_PASTE:
					self._osd.attributes[5] = "Copy/Paste"

				self._osd.attributes[6] = " "
				self._osd.attribute_names[6] = " "
				self._osd.attributes[7] = " "
				self._osd.attribute_names[7] = " "
			else:
				# No clip selected: clear all attributes
				for i in range(8):
					self._osd.attributes[i] = " "
					self._osd.attribute_names[i] = " "

			# Track and Clip Info
			if self._selected_track != None:
				if self._lock_to_track and self._is_locked:
					self._osd.info[0] = "track : " + self._selected_track.name + " (locked)"
				else:
					self._osd.info[0] = "track : " + self._selected_track.name
			else:
				self._osd.info[0] = " "

			if self._clip != None:
				name = self._clip.name
				if name == "":
					name = "(unamed clip)"
				if not self._lock_to_track and self._is_locked:
					self._osd.info[1] = "clip : " + name + " (locked)"
				else:
					self._osd.info[1] = "clip : " + name
			else:
				self._osd.info[1] = "no clip selected"

			self._osd.update()

	def _update_mode_button(self):
		if self.is_enabled():
			if (self._mode_button != None):
				self._mode_button.set_on_off_values("DefaultButton.Disabled")

	def _mode_button_value(self, value, sender):
		pass

	def _clear_side_button_listeners(self):

		for button in self._side_buttons:

			try:
				button.reset()
			except:
				pass

			try:
				button.remove_value_listener(self._mode_button_value)
			except:
				pass

			try:
				button.remove_value_listener(self._sub_mode_value)
			except:
				pass

			try:
				button.remove_value_listener(self._mode_value)
			except:
				pass