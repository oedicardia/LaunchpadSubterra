from _Framework.ControlSurfaceComponent import ControlSurfaceComponent
from _Framework.ButtonMatrixElement import ButtonMatrixElement
from .StepSequencerComponent import StepSequencerComponent, ButtonElement
from .SequencerConstants import (RESOLUTION_NAMES,
    STEPSEQ_MODE_NOTES,
    STEPSEQ_MODE_NOTES_OCTAVES,
	STEPSEQ_MODE_OCTAVE_OVERVIEW,
    STEPSEQ_MODE_COPY_PASTE,
    STEPSEQ_MODE_STEP_VELOCITY_EDITOR,
    STEPSEQ_MODE_STEP_LENGTH_EDITOR,
    STEPSEQ_MODE_VERTICAL_VELOCITY,
    STEPSEQ_MODE_VERTICAL_LENGTH,
)
from .LoopSelectorComponent import LoopSelectorComponent
from .NoteSelectorComponent import NoteSelectorComponent
from .ScaleComponent import MUSICAL_MODES, KEY_NAMES
from .TrackControllerComponent import TrackControllerComponent
from random import randrange
import time
from random import uniform


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
		self.set_mode_copy_paste_button(self._side_buttons[3])
		self._is_copy_paste_shifted = False
		self._last_copy_paste_button_press = time.time()

		self._mode_notes_lengths_button = None
		self.set_mode_notes_lengths_button(self._side_buttons[4])
		self._is_notes_lengths_shifted = False
		self._last_notes_lengths_button_press = time.time()
		self._mode_notes_velocities_button = None

		self.set_mode_notes_velocities_button(self._side_buttons[5])
		self._is_notes_velocity_shifted = False
		self._last_notes_velocity_button_press = time.time()
		self._mode_notes_octaves_button = None

		self.set_mode_notes_octaves_button(self._side_buttons[6])
		self._is_octave_shifted = False
		self._last_notes_octaves_button_press = time.time()



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
		# If you manually remove listeners here, ensure you loop to 8
		if self._matrix != None:
			for x in range(8):
				for y in range(8): # Ensure this matches set_matrix
					button = self._matrix.get_button(x, y)
					try:
						button.remove_value_listener(self._matrix_value)
					except RuntimeError:
						pass
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
	
	
	# def _remove_scale_listeners(self):
	# 	try:
	# 		if self.song():
	# 			self.song().remove_root_note_listener(self.handle_root_note_changed)
	# 			self.song().remove_scale_name_listener(self.handle_scale_name_changed)
	# 	except RuntimeError:
	# 		pass
	
	# def _register_scale_listeners(self):
	# 	try:
	# 		# Only register if we have access to the song object
	# 		if self.song():
	# 			self.song().add_root_note_listener(self.handle_root_note_changed)
	# 			self.song().add_scale_name_listener(self.handle_scale_name_changed)
	# 	except RuntimeError:
	# 		pass

	# def handle_root_note_changed(self):
	# 	# Ensure we update the key indexes when root note changes
	# 	if hasattr(self, '_step_sequencer') and self._step_sequencer and hasattr(self._step_sequencer,
	# 	                                                                         '_scale_selector'):
	# 		self._step_sequencer._scale_selector.set_key(self.song().root_note, False, True)
	# 		self._scale_updated()  # This updates _key_indexes
	# 		self.update()  # Redraw matrix

	# def handle_scale_name_changed(self):
	# 	# Ensure we update keys when scale mode changes
	# 	if hasattr(self, '_step_sequencer') and self._step_sequencer and hasattr(self._step_sequencer,
	# 	                                                                         '_scale_selector'):
	# 		try:
	# 			song_scale = str(self.song().scale_name)
	# 			if song_scale in self._step_sequencer._scale_selector._modus_names:
	# 				mode_idx = self._step_sequencer._scale_selector._modus_names.index(song_scale)
	# 				self._step_sequencer._scale_selector.set_modus(mode_idx, False, True)
	# 				self._scale_updated()
	# 				self.update()
	# 		except (ValueError, AttributeError):
	# 			pass
		

	def set_enabled(self, enabled):
		ControlSurfaceComponent.set_enabled(self, enabled)
		# debug
		# self._control_surface.log_message(
		# 	f"{self.__class__.__name__} enabled={enabled}"
		# )


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
		self._last_velocity_press_time = 0  # Track time of last button press
		self._last_velocity_press_pos = None # Track {x, y} of last button press
		self._double_click_window = 0.25  # 250ms window
		# Track state to revert if double click occurs:
		self._pending_revert_data = None  # Stores the original note list or change log
		self._revert_timer_id = None
		self._last_length_press_time = 0
		self._last_length_press_pos = None
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

		old_mode = self._mode
		self._control_surface.log_message(
			"MODE -> %s from set_mode()" % mode
		)
		self._mode = mode
		self._force_update = True

		# Notify parent
		if hasattr(self, '_step_sequencer') and self._step_sequencer:
			self._step_sequencer.update()

		# --- ACCESS LOOP SELECTOR SAFELY ---
		loop_selector = None
		if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
			loop_selector = self._step_sequencer._loop_selector

		# --- 1: DISABLE LOOP SELECTOR WHEN ENTERING OCTAVE OVERVIEW ---
		if old_mode != STEPSEQ_MODE_OCTAVE_OVERVIEW and mode == STEPSEQ_MODE_OCTAVE_OVERVIEW:
			if loop_selector:
				# 1. Reset internal logic cache to -1 (The component's true "off" state)
				loop_selector.set_enabled(False)
				for i in range(len(loop_selector._buttons)):
					loop_selector._cache[i] = -1  # <--- CHANGE THIS FROM 0 TO -1

				# 2. Clear loop selector button lights explicitly
				for button in loop_selector._buttons:
					if button:
						try:
							button.turn_off()
						except RuntimeError:
							pass

				# 3. Force immediate hardware push
				self._push_to_hardware()

				# 4. Mark for force update on next render
				if hasattr(loop_selector, '_force'):
					loop_selector._force = True

			# Also force force_update flag
			self._force_update = True


		# --- 2: RE-ENABLE LOOP SELECTOR WHEN LEAVING OCTAVE OVERVIEW ---
		elif old_mode == STEPSEQ_MODE_OCTAVE_OVERVIEW and mode != STEPSEQ_MODE_OCTAVE_OVERVIEW:
			if loop_selector:
				if hasattr(self._step_sequencer, '_loop_selector_should_be_enabled'):
					should_enable = self._step_sequencer._loop_selector_should_be_enabled()
				else:
					should_enable = (mode == STEPSEQ_MODE_NOTES)

				if should_enable:
					loop_selector.set_enabled(True)
					if hasattr(loop_selector, '_get_clip_loop'):
						loop_selector._get_clip_loop()
					loop_selector.update()
				else:
					loop_selector.set_enabled(False)
					loop_selector.update()

		# --- 3: CLEAR LOOP SELECTOR VISUALS WHEN EXITING EDITORS TO NORMAL MODE ---
		if (old_mode in [
			STEPSEQ_MODE_STEP_VELOCITY_EDITOR,
			STEPSEQ_MODE_STEP_LENGTH_EDITOR,
			STEPSEQ_MODE_VERTICAL_VELOCITY,
			STEPSEQ_MODE_VERTICAL_LENGTH,
			STEPSEQ_MODE_COPY_PASTE,
			STEPSEQ_MODE_OCTAVE_OVERVIEW
		]) and (mode == STEPSEQ_MODE_NOTES):

			if loop_selector:
				# Clear cache and force update
				loop_selector._cache = [-1] * len(loop_selector._buttons)
				if hasattr(loop_selector, '_force'):
					loop_selector._force = True
				if hasattr(loop_selector, '_get_clip_loop'):
					loop_selector._get_clip_loop()

				# CRITICAL: Force Loop Selector to re-enable immediately if it should own Row 7
				if self._step_sequencer._loop_selector_should_be_enabled():
					loop_selector.set_enabled(True)
					loop_selector._force = True

				# Force a full update cycle
				loop_selector.update()

		# --- 4: NEW - FORCE LOOP SELECTOR REFRESH WHEN ENTERING NOTES MODE ---
		# This ensures Loop Selector LEDs update correctly after velocity/length animations
		elif (mode == STEPSEQ_MODE_NOTES) and (old_mode in [
			STEPSEQ_MODE_STEP_VELOCITY_EDITOR,
			STEPSEQ_MODE_STEP_LENGTH_EDITOR,
			STEPSEQ_MODE_VERTICAL_VELOCITY,
			STEPSEQ_MODE_VERTICAL_LENGTH,
			STEPSEQ_MODE_COPY_PASTE
		]):
			# Entering Notes mode from an editor that temporarily owned Row 7
			if loop_selector:
				if self._step_sequencer._loop_selector_should_be_enabled():
					# Ensure Loop Selector is enabled and refreshed
					if not loop_selector.is_enabled():
						loop_selector.set_enabled(True)

					# Force recalculation of clip loop bounds
					if hasattr(loop_selector, '_get_clip_loop'):
						loop_selector._get_clip_loop()

					# Force immediate update
					if hasattr(loop_selector, '_force'):
						loop_selector._force = True
					loop_selector.update()

		# --- 5: LOGIC FOR ALL OTHER MODE TRANSITIONS ---
		# If entering any mode that owns bottom_row() via uses_bottom_row(), ensure Loop Selector knows
		elif mode in [STEPSEQ_MODE_STEP_VELOCITY_EDITOR, STEPSEQ_MODE_STEP_LENGTH_EDITOR,
		              STEPSEQ_MODE_VERTICAL_VELOCITY, STEPSEQ_MODE_VERTICAL_LENGTH,
		              STEPSEQ_MODE_COPY_PASTE, STEPSEQ_MODE_OCTAVE_OVERVIEW]:
			if loop_selector and self.uses_bottom_row():
				# We now own Row 7, so tell Loop Selector to let go
				if loop_selector.is_enabled():
					loop_selector.set_enabled(False)
					loop_selector._cache = [-1] * len(loop_selector._buttons)
					loop_selector._force = True

		# --- 6: CRITICAL FIX FOR ANIMATION -> EDITOR TRANSITION ---
		# If we are entering an editor mode, ensure ANY active animations are killed
		# and the Loop Selector is fully disabled so it doesn't conflict with the new editor draw.
		if mode in [STEPSEQ_MODE_STEP_VELOCITY_EDITOR, STEPSEQ_MODE_STEP_LENGTH_EDITOR]:
			if loop_selector:
				# Force disable Loop Selector immediately
				loop_selector.set_enabled(False)
				for i in range(len(loop_selector._buttons)):
					loop_selector._cache[i] = -1

				# Physically turn off buttons to clear any "falling head" residue
				for button in loop_selector._buttons:
					if button:
						try:
							button.turn_off()
						except RuntimeError:
							pass

				# Ensure animation flags are definitely false (safety check)
				self._velocity_wait_animation = False
				self._length_wait_animation = False
				self._pending_velocity_editor = False
				self._pending_length_editor = False

				# Force hardware push to update the board instantly
				self._push_to_hardware()

				self._control_surface.log_message(f"[ANIM->EDIT] Killed anim, disabled LS for {mode}")

		# DEBUG LOGGING FOR ROW 7 OWNERSHIP
		self._control_surface.log_message(
			"[set_mode] Row 7 Ownership: %s (mode=%s)" %
			("EDITOR" if self.uses_bottom_row() else "LOOP_SELECTOR", mode)
		)

		self._control_surface.log_message("CALLING UPDATE FROM SET_MODE")
		self.update()

	def _push_to_hardware(self):
		"""Force ALL buffered LED values to hardware immediately, ignoring cache."""
		if self._matrix != None:
			for x in range(8):
				for y in range(8):
					# Always compare back_buffer vs grid_buffer
					if self._grid_back_buffer[x][y] != self._grid_buffer[x][y]:
						self._grid_buffer[x][y] = self._grid_back_buffer[x][y]
						#debug
						button = self._matrix.get_button(x, y)
						if y == 7:
							self._control_surface.log_message(
								"PUSH ROW7 (%d) value=%r type=%s" %
								(
									x,
									self._grid_back_buffer[x][7],
									type(self._grid_back_buffer[x][7]).__name__,
								)
							)
						if button:
							try:
								button.set_light(self._grid_buffer[x][y])
							except RuntimeError:
								pass # Ignore errors if matrix is gone
			# Force the main loop to skip its own update check next time
			self._force_update = False

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

		# Only repaint when this editor is currently visible.
		if self.is_enabled():
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
				self._velocity_map[5],
				False
			)
		)

		self._clip.select_all_notes()
		self._clip.replace_selected_notes(tuple(notes))

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
						note_time = x * self._resolution
						#time = x * self._quantization
						velocity = self._velocity_map[self._notes_velocities[x]]
						length = self._length_map[self._notes_lengths[x]] * self._resolution / 4.0
						#length = self._length_map[self._notes_lengths[x]] * self._quantization / 4.0
						pitch = self._key_indexes[note_index] + 12 * (self._notes_octaves[x] - 2)
						if(pitch >= 0 and pitch < 128 and velocity >= 0 and velocity < 128 and length >= 0):
							note_cache.append([pitch, note_time, length, velocity, False])
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
		# if not self._initializing:
		# 	self._control_surface.log_message(
		# 		"UPDATE CALLED mode=%s" % self._mode
		# 	)
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
				for y in range(8):
					button = self._matrix.get_button(x, y)
					button.add_value_listener(
						self._matrix_value,
						identify_sender=True
					)
		# Log to confirm scale selector exists on parent
		if hasattr(self, '_step_sequencer') and self._step_sequencer:
			self._control_surface.log_message(f"PARENT HAS SCALE SELECTOR: {hasattr(self._step_sequencer, '_scale_selector')}")
		else:
			self._control_surface.log_message("NO PARENT COMPONENT!")
		self._force_update = True

		# debug
		# self._control_surface.log_message("MATRIX ASSIGNED")

	def _update_matrix(self):  # step grid LEDs are updated here
		self._control_surface.log_message(
			"UPDATE_MATRIX mode=%d" % self._mode
		)
		# self._control_surface.log_message(
		# 	"UPDATE mode=%d playhead=%s"
		# 	% (self._mode, self._playhead)
		# )
		# self._control_surface.log_message(
		# 	f"[_update_matrix] MODE={self._mode}, ENABLED={self.is_enabled()}, FORCE={self._force_update}")

		# Check scale selector availability
		if hasattr(self, '_step_sequencer') and self._step_sequencer:
			has_scale = hasattr(self._step_sequencer, '_scale_selector') and self._step_sequencer._scale_selector
			# self._control_surface.log_message(f"[UPDATE_MATRIX] SCALE_SELECTOR AVAILABLE: {has_scale}")
			if has_scale:
				try:
					root_key = self._step_sequencer._scale_selector._key
					self._control_surface.log_message(f"[UPDATE_MATRIX] ROOT_KEY={root_key}")
				except AttributeError as e:
					self._control_surface.log_message(f"[UPDATE_MATRIX] ERROR ACCESSING ROOT_KEY: {e}")

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
						self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"
					continue

				# --- CONFIGURATION ---
				SPAWN_COEFFICIENT = 2.5  # Tune this: Lower = faster spawns
				FALL_SPEED = 12.0

				time_to_fall = 8.0 / FALL_SPEED
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
					if head_pos > 7: head_pos = 7

				# Clear Column (Always Black first)
				for y in range(7):
					self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
					self._grid_back_buffer[x][y] = "DefaultButton.Disabled"

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
						self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"
					continue

				# Configuration
				SPAWN_COEFFICIENT = 2.0
				FALL_SPEED = 12.0
				time_to_fall = 8.0 / FALL_SPEED
				base_wait = ((x + 1) * 0.43) % 1.0
				random_wait = base_wait * SPAWN_COEFFICIENT  # Scaled by coefficient

				cycle_duration = time_to_fall + random_wait
				time_in_cycle = elapsed_col % cycle_duration

				head_pos = -1
				if time_in_cycle < time_to_fall:
					pos_float = (time_in_cycle / time_to_fall) * 7.0
					head_pos = int(pos_float)
					if head_pos > 7: head_pos = 7

				for y in range(7):
					self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
					self._grid_back_buffer[x][y] = "DefaultButton.Disabled"

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
				#self._control_surface.log_message("CLEAR BUFFER")
				for x in range(8):
					for y in range(7):
						#self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"

				# update back buffer
				if self._clip != None:

					for x in range(8):
						has_note = False
						idx = self._get_step_index(x)
						# detect if column has any note
						for y in range(7):
							if self._notes_pitches[(idx) * 7 + 6 - y] == 1:
								has_note = True
						if self._mode == STEPSEQ_MODE_NOTES:
							# debug
							#self._control_surface.log_message("DRAW NOTES")
							# --- SCALE CONFIGURATION ---
							scale_root_key = 0
							scale_notes = [0, 2, 4, 5, 7, 9, 11]

							if hasattr(self, "_step_sequencer") and self._step_sequencer and \
									hasattr(self._step_sequencer, "_scale_selector") and \
									self._step_sequencer._scale_selector:
								selector = self._step_sequencer._scale_selector
								scale_root_key = selector._key
								scale_notes = [n % 12 for n in selector.notes]

							idx = self._get_step_index(x)
							start_time = idx * self._resolution
							end_time = start_time + self._resolution
							step_notes_list = self._get_notes_at_step(idx)

							for y in range(7):
								row_idx = 6 - y
								current_pitch = (self._key_indexes[row_idx] + 12 * (self._display_octave - 2))

								# --- NEW LOGIC: Find notes that map to THIS row ---
								notes_mapped_to_this_row = []

								for n in step_notes_list:
									note_midi = n[0]
									mapped_row = self._get_row_for_pitch(note_midi)

									# This note belongs to this row if it's close enough AND in this time step
									if mapped_row == row_idx and start_time <= n[1] < end_time:
										notes_mapped_to_this_row.append(n)

								has_note_here = len(notes_mapped_to_this_row) > 0

								if not has_note_here:
									interval = (current_pitch - scale_root_key) % 12
									is_root = (interval == 0)
									is_chord_tone = (interval == scale_notes[2] % 12 or interval == scale_notes[4] % 12)

									if is_root:
										self._grid_back_buffer[x][y] = "StepSequencer2.Pitch.RootNote"
									elif is_chord_tone:
										self._grid_back_buffer[x][y] = "StepSequencer2.Pitch.ChordTone"
									else:
										self._grid_back_buffer[x][y] = "StepSequencer2.Pitch.Off"
									continue

								# --- NOTE PRESENT LOGIC ---
								has_on_step = False
								has_off_step = False
								has_in_scale = False
								has_out_scale = False

								for n in notes_mapped_to_this_row:
									note_midi = n[0]
									note_time = n[1]
									note_deg = note_midi % 12

									if self._is_note_on_grid(note_time, self._resolution):
										has_on_step = True
									else:
										has_off_step = True

									if note_deg in scale_notes:
										has_in_scale = True
									else:
										has_out_scale = True

								# --- COLOR DETERMINATION ---
								color_key = "StepSequencer2.Pitch.On"

								if has_out_scale and has_off_step:
									color_key = "StepSequencer2.Pitch.OnMixedStepScale"
								elif has_in_scale and has_out_scale and not has_off_step:
									color_key = "StepSequencer2.Pitch.OnMixedScale"
								elif has_on_step and has_off_step and not has_out_scale:
									color_key = "StepSequencer2.Pitch.OnMixedStep"
								elif has_out_scale and not has_in_scale and not has_off_step:
									color_key = "StepSequencer2.Pitch.OnOutScale"  # GREEN
								elif has_off_step and not has_on_step and not has_out_scale:
									color_key = "StepSequencer2.Pitch.OnOffStep"  # RED
								else:
									color_key = "StepSequencer2.Pitch.On"  # BLUE

								#self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, color_key))
								self._grid_back_buffer[x][y] = color_key

						elif self._mode == STEPSEQ_MODE_NOTES_OCTAVES:
							# OCTAVE MODE LOGIC
							for y in range(7):
								has_note_in_row = (self._notes_pitches[(idx * 7) + (6 - y)] == 1)

								if has_note_in_row:
									if self._notes_octaves[idx] == (6 - y):
										color_octave = "StepSequencer2.Octave.On"
									else:
										color_octave = "StepSequencer2.Octave.Off"
								else:
									if self._notes_octaves[idx] == (6 - y):
										color_octave = "StepSequencer2.Octave.Dim"
									else:
										color_octave = "StepSequencer2.Octave.Off"

								#self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, color_octave))
								self._grid_back_buffer[x][y] = color_octave


						elif self._mode == STEPSEQ_MODE_OCTAVE_OVERVIEW:
							#self._control_surface.log_message("DRAW OCTAVE OVERVIEW")
							for x in range(8):
								idx = self._get_step_index(x)
								step_notes_list = self._get_notes_at_step(idx)
								# for y in range(8):
								# 	self._grid_back_buffer[x][y] = "DefaultButton.Disabled"

								BASE_OCTAVE = 8

								for y in range(8):
									target_octave = BASE_OCTAVE - y
									has_note_in_octave = False

									# Check all notes in this step
									for n in step_notes_list:
										note_octave = int(n[0] / 12)
										if note_octave == target_octave:
											has_note_in_octave = True
											break

									# Determine Target Color
									target_color = "StepSequencer2.Octave.On" if has_note_in_octave else 0

									# 1. Update Back Buffer
									#self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, target_color))
									self._grid_back_buffer[x][y] = target_color

						else:
							for y in range(8):
								if self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR:
									#self._control_surface.log_message("DRAWING VELOCITY EDITOR")

									# Safety check
									if self._editing_step is None:
										return

									idx = self._editing_step

									# --- PRE-CALCULATE MIXED VELOCITIES PER PITCH ---
									# Dictionary: { pitch_code : True/False }
									mixed_velocity_pitches = {}

									# Get all notes for the current step
									step_notes = self._get_notes_at_step(idx)

									# Group by pitch and check for velocity conflicts
									pitch_velocities = {} # { pitch_code : [vel1, vel2, ...] }

									for note in step_notes:
										note_pitch = note[0]
										note_vel = note[3]

										if note_pitch not in pitch_velocities:
											pitch_velocities[note_pitch] = []
										pitch_velocities[note_pitch].append(note_vel)

									# Identify pitches with mixed velocities
									for pitch, vels in pitch_velocities.items():
										if len(vels) > 1:
											# Check if any two velocities differ
											if len(set(vels)) > 1:
												mixed_velocity_pitches[pitch] = True
											else:
												mixed_velocity_pitches[pitch] = False
										else:
											mixed_velocity_pitches[pitch] = False

									# --- DRAW THE GRID ---
									for y in range(7):
										pitch = self._pitch_for_row(y)
										note = self._get_note_for_pitch_at_step(self._editing_step, pitch)

										# Check if THIS specific pitch has mixed velocities
										is_conflict = mixed_velocity_pitches.get(pitch, False)

										if note is None:
											# No note here: draw dim grid background
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

										# There is a note (or notes) at this pitch
										velocity = note[3] # Note: If mixed, 'note' usually returns the first one found.
														   # But the color logic relies on 'is_conflict', not the specific value here.

										bucket = 0
										for i, v in enumerate(self._velocity_map):
											if velocity >= v:
												bucket = i

										for col in range(8):
											if col <= bucket:
												if is_conflict:
													# USE RED COLOR FOR CONFLICTS
													self._grid_back_buffer[col][y] = "StepSequencer2.Velocity.Mixed"
												else:
													# Normal On Color
													self._grid_back_buffer[col][y] = "StepSequencer2.Velocity.On"
											else:
												# Normal Off/Dim Color
												self._grid_back_buffer[col][y] = "StepSequencer2.Velocity.Off"

								elif self._mode == STEPSEQ_MODE_VERTICAL_VELOCITY:
									# VERTICAL VELOCITY DISPLAY
									# Each column shows the MAX velocity of any note in that step as a vertical bar.
									# RED COLOR if notes in the step have DIFFERENT velocities

									for col_x in range(8):
										idx = self._get_step_index(col_x)
										step_notes = self._get_notes_at_step(idx)

										max_vel_idx = -1 # -1 indicates NO notes found
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

											# Clamp to grid height (8 rows = indices 0-7)
											if max_vel_idx > 7: max_vel_idx = 7

											# CHECK IF VELOCITIES ARE MIXED
											if len(velocity_buckets) > 1:
												# Multiple velocity buckets found in this step
												first_bucket = velocity_buckets[0]
												for b in velocity_buckets[1:]:
													if b != first_bucket:
														has_mixed_velocities = True
														break

										# Draw the vertical bar with appropriate color
										for row_y in range(8):
											row_bucket = 7 - row_y

											if max_vel_idx == -1:
												# CASE: NO NOTES IN THIS STEP
												if row_y <= 1:
													# Rows 0 and 1: BLACK
													self._grid_back_buffer[col_x][row_y] = 0
												else:
													# Rows 2 to 7: DIMMED
													self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Velocity.Dim"
											else:
												# CASE: HAS NOTES
												if has_mixed_velocities and row_bucket <= max_vel_idx:
													# RED for mixed
													self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Velocity.Mixed"
												elif row_bucket <= max_vel_idx:
													# BRIGHT bar
													self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Velocity.On"
												else:
													# Above the bar: BLACK
													self._grid_back_buffer[col_x][row_y] = 0

								elif self._mode == STEPSEQ_MODE_VERTICAL_LENGTH:
									# VERTICAL LENGTH DISPLAY

									for col_x in range(8):
										idx = self._get_step_index(col_x)
										step_notes = self._get_notes_at_step(idx)

										max_len_idx = -1
										has_mixed_lengths = False

										if step_notes:
											length_buckets = []
											for note in step_notes:
												note_len = note[2]
												bucket = 0
												# Find bucket: compare note_len against length_map * resolution/4.0
												for i, v in enumerate(self._length_map):
													if note_len >= v * self._resolution / 4.0:
														bucket = i
												length_buckets.append(bucket)

												if bucket > max_len_idx:
													max_len_idx = bucket

											if max_len_idx > 7: max_len_idx = 7

											# CHECK IF MIXED
											if len(length_buckets) > 1:
												if len(set(length_buckets)) > 1:
													has_mixed_lengths = True

										# Draw bar
										for row_y in range(8):
											# FIXED: Row 0 (Top) should represent highest bucket, Row 7 (Bottom) lowest
											row_bucket = 7 - row_y

											if max_len_idx == -1:
												# NO NOTES
												if row_y <= 1:
													self._grid_back_buffer[col_x][row_y] = 0
												else:
													self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Length.Dim"
											else:
												# HAS NOTES
												if has_mixed_lengths and row_bucket <= max_len_idx:
													self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Velocity.Mixed"
												elif row_bucket <= max_len_idx:
													self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Length.On"
												else:
													self._grid_back_buffer[col_x][row_y] = 0

								elif self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
									if self._editing_step is None:
										return

									# --- PRE-CALCULATE MIXED LENGTHS PER PITCH ---
									# Dictionary: { pitch_code : True/False }
									mixed_length_pitches = {}
									step_notes = self._get_notes_at_step(self._editing_step)

									# Group by pitch and check for length conflicts
									pitch_lengths = {}  # { pitch_code : [length1, length2, ...] }

									for note in step_notes:
										note_pitch = note[0]
										note_len = note[2]  # Index 2 is Duration

										if note_pitch not in pitch_lengths:
											pitch_lengths[note_pitch] = []
										pitch_lengths[note_pitch].append(note_len)

									# Identify pitches with mixed lengths
									for pitch, lengths in pitch_lengths.items():
										if len(lengths) > 1:
											# Check if any two lengths differ
											if len(set(lengths)) > 1:
												mixed_length_pitches[pitch] = True
											else:
												mixed_length_pitches[pitch] = False
										else:
											mixed_length_pitches[pitch] = False

									# --- DRAW THE GRID ---
									for y in range(7):
										pitch = self._pitch_for_row(y)

										# We don't just get ONE note anymore; we might have multiple,
										# but we draw based on the *max* length or a representative one,
										# while using 'is_conflict' for color.
										# Let's find the max length bucket for this pitch to draw the bar height.

										current_note = self._get_note_for_pitch_at_step(self._editing_step, pitch)

										# Check if THIS specific pitch has mixed lengths
										is_mixed = mixed_length_pitches.get(pitch, False)

										if current_note is None:
											# No note here: draw dim grid background
											default_length_idx = 3
											for col in range(8):
												if col <= default_length_idx:
													self._grid_back_buffer[col][y] = "StepSequencer2.Length.Dim"
												else:
													self._grid_back_buffer[col][
														y] = "StepSequencer2.Length.Off"
											continue

										# There is a note (or notes) at this pitch
										# If mixed, we usually draw the MAXIMUM length to show the full extent,
										# or the first one found. Let's use the max length for visual clarity.
										pitch_max_length = 0
										for note in step_notes:
											if note[0] == pitch:
												if note[2] > pitch_max_length:
													pitch_max_length = note[2]

										# Calculate bucket for the max length
										length_bucket = 0
										for i, v in enumerate(self._length_map):
											if pitch_max_length >= v * self._resolution / 4.0:
												length_bucket = i

										if length_bucket > 7: length_bucket = 7

										# Draw the "Bar" across the columns for this row
										for col in range(8):
											if col <= length_bucket:
												if is_mixed:
													# USE RED COLOR FOR CONFLICTS
													# Note: Ensure you have defined "StepSequencer2.Length.Mixed" in your resources
													# If not, you might need to reuse Velocity.Mixed or create a new string
													self._grid_back_buffer[col][y] = "StepSequencer2.Length.Mixed"
												else:
													# Normal On Color
													self._grid_back_buffer[col][y] = "StepSequencer2.Length.On"
											else:
												# Normal Off/Dim Color
												self._grid_back_buffer[col][y] = "StepSequencer2.Length.Dim"

				# --- METRONOME ---
				# playing notes (ONLY current visible page and Normal Mode)
				if self._playhead != None and self._mode == STEPSEQ_MODE_NOTES:
					if hasattr(self, '_resolution'):
						res = self._resolution
					elif hasattr(self._step_sequencer, '_resolution'):
						res = self._step_sequencer._resolution
					else:
						res = 0.25  # Default fallback

					play_position = int(self._playhead / res)
					play_x_position = play_position % 8
					page = int(play_position / 8)

					if page == effective_page:
						metronome_color = "StepSequencer2.NoteEditor.MetronomeInPage"

						# full column metronome
						for y in range(7):
							self._grid_back_buffer[play_x_position][y] = metronome_color

						for y in range(7):
							if self._notes_pitches[play_position * 7 + 6 - y] == 1:
								self._grid_back_buffer[play_x_position][y] = "StepSequencer2.NoteEditor.PlayInPage"

			else:
				for x in range(8):
					for y in range(7):
						#self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"

		# self._control_surface.log_message(
		# 	"PUSH mode=%d" % self._mode
		# )
		# --- PUSH TO HARDWARE (cache optimization) ---
		if self._matrix != None:
			#push_count = 0
			for x in range(8):
				for y in range(8):
					if y == 7 and not self.uses_bottom_row():
						continue  # Skip Row 7 - let Loop Selector handle it
					button = self._matrix.get_button(x, y)

					# self._control_surface.log_message(
					# 	"PUSH (%d,%d) -> %s" % (
					# 		x,
					# 		y,
					# 		str(self._grid_back_buffer[x][y])
					# 	)
					# )
					# # debug
					#
					# if y == 7:
					# 	self._control_surface.log_message(
					# 		"PUSH ROW7 (%d) value=%r type=%s" %
					# 		(
					# 			x,
					# 			self._grid_back_buffer[x][7],
					# 			type(self._grid_back_buffer[x][7]).__name__,
					# 		)
					# 	)
					# self._control_surface.log_message(
					# 	"BUTTON (%d,%d): id=%s obj=%s"
					# 	% (
					# 		x,
					# 		y,
					# 		button.identifier,
					# 		hex(id(button))
					# 	)
					# )
					# self._control_surface.log_message(
					# 	"BUTTON CLASS=%s" % button.__class__.__name__
					# )
					#
					# self._control_surface.log_message(
					# 	"BUTTON MODULE=%s" % button.__class__.__module__
					# )

					button.set_light(self._grid_back_buffer[x][y])

					# if self._grid_back_buffer[x][y] != self._grid_buffer[x][y] or self._force_update:
					# 	self._grid_buffer[x][y] = self._grid_back_buffer[x][y]
					# 	self._matrix.get_button(x, y).set_light(self._grid_buffer[x][y])
					# 	push_count += 1

			# if push_count > 0 or self._force_update:
			# 	self._control_surface.log_message(
			# 		f"[PUSH_TO_HW] Updated {push_count} lights, force={self._force_update}")

			self._force_update = False

		# for x in range(8): # when this is on the whole grid is black, no matter the mode
		# 	for y in range(8):
		# 		self._matrix.get_button(x, y).set_light("DefaultButton.Disabled")

	def _matrix_value(self, value, sender):  # matrix buttons listener
		# Identify x and y coordinates from the sender
		x = -1
		y = -1
		for xx in range(8):
			for yy in range(8):
				if self._matrix.get_button(xx, yy) == sender:
					x = xx
					y = yy

		# DEBUG: Verify we are receiving the click on row 7
		#self._control_surface.log_message(f"_matrix_value received: x={x}, y={y}")

		# --- BLOCK ROW 7 ONLY IN HORIZONTAL EDITORS ---
		if (self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR or
				self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR):
			if y == 7:
				return  # Silently ignore Row 7 press in Horizontal modes


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
				if self._velocity_wait_animation and ((value != 0) or (not sender.is_momentary())):
					idx = self._get_step_index(x)

					# Stop Animation
					self._velocity_wait_animation = False
					self._mode_notes_velocities_button.turn_off()  # Ensure button LED turns off

					# Set up Horizontal Editor state
					self._pending_velocity_editor = False
					self._editing_step = idx
					self.set_mode(STEPSEQ_MODE_STEP_VELOCITY_EDITOR)

					self._control_surface.show_message("Horizontal Velocity Step %d" % idx)
					self._update_matrix()
					return  # Exit function to prevent further processing

				# --- EXISTING PENDING LOGIC (For Length Editor mostly) ---
				if self._pending_velocity_editor:
					# This block is now primarily a fallback if animation logic was bypassed
					idx = self._get_step_index(x)
					self._editing_step = idx

					# self._control_surface.log_message(
					# 	"ENTERING VELOCITY EDITOR step=%s" % idx
					# )
					self._velocity_wait_animation = False
					self._pending_velocity_editor = False
					self.set_mode(STEPSEQ_MODE_STEP_VELOCITY_EDITOR)
					return

				# If we are currently animating (waiting for user selection),
				# pressing a grid button stops the animation and enters Horizontal Editor immediately.
				if self._length_wait_animation and ((value != 0) or (not sender.is_momentary())) and y < 7:
					idx = self._get_step_index(x)

					# Stop Animation
					self._length_wait_animation = False
					self._mode_notes_velocities_button.turn_off()  # Ensure button LED turns off

					# Set up Horizontal Editor state
					self._pending_length_editor = False
					self._editing_step = idx
					self.set_mode(STEPSEQ_MODE_STEP_LENGTH_EDITOR)

					self._control_surface.show_message("Horizontal Length Step %d" % idx)
					self._update_matrix()
					return  # Exit function to prevent further processing

				if self._pending_length_editor:
					idx = self._get_step_index(x)
					self._editing_step = idx
					self._pending_length_editor = False
					self._length_wait_animation = False
					self.set_mode(STEPSEQ_MODE_STEP_LENGTH_EDITOR)
					return

				# --- MAIN BUTTON PRESS HANDLING ---
				allow_row7 = (self._mode in (STEPSEQ_MODE_VERTICAL_VELOCITY,STEPSEQ_MODE_VERTICAL_LENGTH))

				if ((value != 0) or (not sender.is_momentary())):
					if y == 7 and not allow_row7:
						return
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
						# HORIZONTAL VELOCITY MODE WITH DOUBLE-CLICK DELETE

						# Use a timestamp variable name to avoid conflict if 'time' is used locally
						current_ts = time.time()
						pitch = self._pitch_for_row(y)
						step_idx = self._editing_step

						# Check for Double Click
						is_double_click = False
						if (self._last_velocity_press_pos is not None and
						    self._last_velocity_press_pos['x'] == x and
						    self._last_velocity_press_pos['y'] == y and
						    (current_ts - self._last_velocity_press_time) < self._double_click_window):
							is_double_click = True

						# Update tracking state for next press
						self._last_velocity_press_time = current_ts
						self._last_velocity_press_pos = {'x': x, 'y': y}

						if is_double_click:
							# --- DELETE OPERATION (DELETE LAST NOTE IN STEP) ---
							step_idx = self._editing_step
							start_time = step_idx * self._resolution
							end_time = start_time + self._resolution

							notes = list(self._note_cache)

							# Find the note with the HIGHEST time value matching the pitch and step
							target_index = -1
							max_note_time = -1.0

							for i, note in enumerate(notes):
								note_p, note_t, note_l, note_v, note_m = note

								if (note_p == pitch and
										start_time <= note_t < end_time):

									# Keep track of the note with the latest time
									if note_t > max_note_time:
										max_note_time = note_t
										target_index = i

							if target_index != -1:
								notes.pop(target_index)
								self._write_note_cache_to_clip(notes)
								self._update_matrix()
								self._control_surface.show_message("Note deleted")
							return

						else:
							# --- SET VELOCITY OPERATION ---
							velocity = self._velocity_map[x]
							note = self._get_note_for_pitch_at_step(step_idx, pitch)

							if note is None:
								self._add_note_at_step(step_idx, pitch, velocity)
							else:
								self._set_velocity_for_pitch_at_step(step_idx, pitch, velocity)

							self._update_matrix()
							return

					elif self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
						# HORIZONTAL LENGTH MODE WITH DOUBLE-CLICK DELETE

						current_ts = time.time()
						pitch = self._pitch_for_row(y)
						step_idx = self._editing_step

						# Check for Double Click
						is_double_click = False
						if (self._last_length_press_pos is not None and
						    self._last_length_press_pos['x'] == x and
						    self._last_length_press_pos['y'] == y and
						    (current_ts - self._last_length_press_time) < self._double_click_window):
							is_double_click = True

						# Update tracking
						self._last_length_press_time = current_ts
						self._last_length_press_pos = {'x': x, 'y': y}

						if is_double_click:
							# --- DELETE OPERATION ---
							step_idx = self._editing_step
							start_time = step_idx * self._resolution
							end_time = start_time + self._resolution

							notes = list(self._note_cache)
							target_index = -1
							max_note_time = -1.0

							for i, note in enumerate(notes):
								note_p, note_t, note_l, note_v, note_m = note
								if (note_p == pitch and start_time <= note_t < end_time):
									if note_t > max_note_time:
										max_note_time = note_t
										target_index = i

							if target_index != -1:
								notes.pop(target_index)
								self._write_note_cache_to_clip(notes)
								self._parse_notes()
								self._update_matrix()
								self._control_surface.show_message("Note deleted")
							return

						else:
							# --- SET LENGTH OPERATION ---
							# Calculate target length based on column X
							len_bucket = x
							if len_bucket > 7: len_bucket = 7
							target_length = self._length_map[len_bucket] * self._resolution / 4.0

							start_time = step_idx * self._resolution
							end_time = start_time + self._resolution

							# Double Click Check (Delete Logic) - Keep your existing double click logic here

							# Single Click Logic:
							note = self._get_note_for_pitch_at_step(step_idx, pitch)

							if note is None:
								# CREATE NEW NOTE WITH DEFAULT VELOCITY 90 AND TARGET LENGTH
								new_note = (pitch, start_time, target_length, 90, False)
								notes = list(self._note_cache)
								notes.append(new_note)
								self._write_note_cache_to_clip(notes)
								self._note_cache = tuple(notes)
								self._parse_notes() # Critical: Update the grid buffers
							else:
								# UPDATE EXISTING NOTE'S LENGTH ONLY (Preserves velocity)
								self._set_length_for_pitch_at_step(step_idx, pitch, target_length)
								# No need to parse_notes here if _set_length_for_pitch_at_step handles cache updates properly,
								# but calling it ensures safety.
								self._parse_notes()

							self._update_matrix()
							return

					elif self._mode == STEPSEQ_MODE_VERTICAL_VELOCITY:
						# VERTICAL VELOCITY MODE - INSTANT EXECUTE WITH REVERT ON DOUBLE CLICK

						# DEBUG: Verify we are receiving the click on row 7
						#self._control_surface.log_message(f"VERT VEL MODE CLICK: x={x}, y={y}")

						current_ts = time.time()
						step_idx = idx

						vel_bucket = 7 - y
						if vel_bucket < 0: vel_bucket = 0
						if vel_bucket > 7: vel_bucket = 7
						target_velocity = self._velocity_map[vel_bucket]

						start_time = step_idx * self._resolution
						end_time = start_time + self._resolution

						# --- DETECT DOUBLE CLICK ---
						is_double_click = False
						if (self._last_velocity_press_pos is not None and
						    self._last_velocity_press_pos['x'] == x and
						    self._last_velocity_press_pos['y'] == y and
						    (current_ts - self._last_velocity_press_time) < self._double_click_window):
							is_double_click = True

						# Update tracking
						self._last_velocity_press_time = current_ts
						self._last_velocity_press_pos = {'x': x, 'y': y}

						if is_double_click:
							# --- HANDLE DOUBLE CLICK ---

							# 1. REVERT to the saved OLD state
							if self._pending_revert_data is not None:
								self._write_note_cache_to_clip(self._pending_revert_data)
								self._note_cache = tuple(self._pending_revert_data)
								# CRITICAL: Refresh display data from cache
								self._parse_notes()

								self._pending_revert_data = None

							# 2. PERFORM DELETE on the NOW-CORRECTED cache
							current_notes = list(self._note_cache)
							target_index = -1
							max_note_time = -1.0

							for i, note in enumerate(current_notes):
								note_p, note_t, note_l, note_v, note_m = note
								if (start_time <= note_t < end_time and note_v == target_velocity):
									if note_t > max_note_time:
										max_note_time = note_t
										target_index = i

							if target_index != -1:
								current_notes.pop(target_index)
								self._write_note_cache_to_clip(current_notes)
								self._note_cache = tuple(current_notes)
								# CRITICAL: Refresh display data from cache
								self._parse_notes()

								self._update_matrix()
								self._control_surface.show_message("Note deleted")

							return

						else:
							# --- HANDLE SINGLE CLICK ---

							# 1. SAVE CURRENT STATE for potential revert
							self._pending_revert_data = list(self._note_cache)

							# 2. IMMEDIATELY APPLY THE CHANGE
							notes = list(self._note_cache)
							changed = False
							for i, note in enumerate(notes):
								note_p, note_t, note_l, old_vel, muted = note
								if start_time <= note_t < end_time:
									if old_vel != target_velocity:
										notes[i] = (note_p, note_t, note_l, target_velocity, muted)
										changed = True

							if changed:
								self._write_note_cache_to_clip(notes)
								self._note_cache = tuple(notes)
								# CRITICAL: Refresh display data from cache
								self._parse_notes()
								self._update_matrix()

							return

					elif self._mode == STEPSEQ_MODE_VERTICAL_LENGTH:
						# VERTICAL LENGTH MODE - INSTANT EXECUTE WITH REVERT ON DOUBLE CLICK

						current_ts = time.time()
						step_idx = idx

						# Map Row Y (0-7) to Length Bucket
						# Row 0 (Top) = High Index (Long), Row 7 (Bottom) = Low Index (Short)
						len_bucket = 7 - y
						if len_bucket < 0: len_bucket = 0
						if len_bucket > 7: len_bucket = 7

						target_length = self._length_map[len_bucket] * self._resolution / 4.0

						start_time = step_idx * self._resolution
						end_time = start_time + self._resolution

						# --- DETECT DOUBLE CLICK ---
						is_double_click = False
						if (self._last_length_press_pos is not None and
								self._last_length_press_pos['x'] == x and
								self._last_length_press_pos['y'] == y and
								(current_ts - self._last_length_press_time) < self._double_click_window):
							is_double_click = True

						# Update tracking
						self._last_length_press_time = current_ts
						self._last_length_press_pos = {'x': x, 'y': y}

						if is_double_click:
							# --- HANDLE DOUBLE CLICK (DELETE SINGLE NOTE) ---

							# 1. Revert state if needed (optional safety)
							if self._pending_revert_data is not None:
								self._write_note_cache_to_clip(self._pending_revert_data)
								self._note_cache = tuple(self._pending_revert_data)
								self._parse_notes()
								self._pending_revert_data = None

							# 2. Find the target note to delete
							# Logic: Delete the note in this step that matches the target length bucket
							#        AND has the HIGHEST time value (latest creation order).

							current_notes = list(self._note_cache)
							target_index = -1
							max_note_time = -1.0

							for i, note in enumerate(current_notes):
								note_p, note_t, note_l, note_v, note_m = note

								# Check if note is in this step
								if start_time <= note_t < end_time:
									# Calculate the bucket of this existing note
									existing_len_bucket = 0
									for i_map, v in enumerate(self._length_map):
										if note_l >= v * self._resolution / 4.0:
											existing_len_bucket = i_map

									# Check if it matches the clicked row's bucket
									if existing_len_bucket == len_bucket:
										# Keep track of the note with the LATEST time
										if note_t > max_note_time:
											max_note_time = note_t
											target_index = i

							# 3. Perform deletion on exactly ONE note
							if target_index != -1:
								current_notes.pop(target_index)
								self._write_note_cache_to_clip(current_notes)
								self._note_cache = tuple(current_notes)
								self._parse_notes()
								self._update_matrix()
								self._control_surface.show_message("Note deleted")
							else:
								self._control_surface.show_message("No matching note found")

							return

						else:
							# --- HANDLE SINGLE CLICK (SET LENGTH FOR ALL IN STEP) ---

							# 1. SAVE CURRENT STATE
							self._pending_revert_data = list(self._note_cache)

							# 2. APPLY CHANGE TO ALL NOTES IN THIS STEP
							notes = list(self._note_cache)
							changed = False

							for i, note in enumerate(notes):
								note_p, note_t, note_l, note_v, note_m = note

								if start_time <= note_t < end_time:
									# Only change if different
									if abs(note_l - target_length) > 0.001:
										notes[i] = (note_p, note_t, target_length, note_v, note_m)
										changed = True

							if changed:
								self._write_note_cache_to_clip(notes)
								self._note_cache = tuple(notes)
								self._parse_notes()
								self._update_matrix()

							return

					elif self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
						current_ts = time.time()
						pitch = self._pitch_for_row(y)
						step_idx = self._editing_step

						# Calculate target length from column X (mirroring velocity's use of x)
						len_bucket = x  # Column 0-7 maps directly to length_map index
						if len_bucket > 7: len_bucket = 7

						target_length = self._length_map[len_bucket] * self._resolution / 4.0

						start_time = step_idx * self._resolution
						end_time = start_time + self._resolution

						# --- DOUBLE CLICK DELETE ---
						is_double_click = False
						if (self._last_length_press_pos is not None and
							self._last_length_press_pos['x'] == x and
							self._last_length_press_pos['y'] == y and
							(current_ts - self._last_length_press_time) < self._double_click_window):
							is_double_click = True

						self._last_length_press_time = current_ts
						self._last_length_press_pos = {'x': x, 'y': y}

						if is_double_click:
							# Delete all notes in this step (simpler than velocity which deletes by pitch)
							# Or delete by matching length like velocity matches velocity?
							# Let's mirror velocity: Delete the specific note at this pitch/step
							notes = list(self._note_cache)
							target_index = -1
							max_note_time = -1.0

							for i, note in enumerate(notes):
								n_p, n_t, n_l, n_v, n_m = note
								if (n_p == pitch and start_time <= n_t < end_time):
									if n_t > max_note_time:
										max_note_time = n_t
										target_index = i

							if target_index != -1:
								notes.pop(target_index)
								self._write_note_cache_to_clip(notes)
								self._note_cache = tuple(notes)
								self._parse_notes()  # CRITICAL: Refresh internal buffers
								self._update_matrix()
								self._control_surface.show_message("Note deleted")
							return

						else:
							# --- SET LENGTH ---
							# Mirror velocity: Save pending, then apply change

							self._pending_revert_data = list(self._note_cache)

							note = self._get_note_for_pitch_at_step(step_idx, pitch)

							if note is None:
								# CREATE NEW NOTE WITH DEFAULT VELOCITY 90 AND TARGET LENGTH
								new_note = (pitch, start_time, target_length, 90, False)
								notes = list(self._note_cache)
								notes.append(new_note)
								self._write_note_cache_to_clip(notes)
								self._note_cache = tuple(notes)
							else:
								# UPDATE EXISTING NOTE'S LENGTH ONLY
								self._set_length_for_pitch_at_step(step_idx, pitch, target_length)

							self._parse_notes()  # CRITICAL: Refresh after modifications
							self._update_matrix()
							return

					# Default handling for other modes (Copy/Paste, etc.)
					self._update_matrix()

					if self._mode == STEPSEQ_MODE_NOTES:
						self._toggle_note_at_grid_position(idx, y)
						return
					else:
						self._update_clip_notes()

	def _get_row_for_pitch(self, midi_note):
		"""
        Find which grid row (0-6) corresponds closest to this MIDI note.
        Returns -1 if no reasonable mapping exists.
        """
		# Get all possible pitches on the grid
		grid_pitches = []
		for row_idx in range(7):
			pitch = self._key_indexes[row_idx] + 12 * (self._display_octave - 2)
			grid_pitches.append((pitch, row_idx))

		# Find closest pitch
		min_distance = float('inf')
		closest_row = -1

		for grid_pitch, row_idx in grid_pitches:
			distance = abs(midi_note - grid_pitch)
			if distance < min_distance:
				min_distance = distance
				closest_row = row_idx

		return closest_row

	def _is_note_on_grid(self, note_time, resolution):
		# Tolerance for floating point inaccuracies (e.g., 0.0001)
		tolerance = 0.001
		remainder = abs(note_time % resolution)
		return remainder < tolerance or remainder > (resolution - tolerance)

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

			note_pitch, note_time, length, old_velocity, muted = note

			if (note_pitch == pitch and start_time <= note_time < end_time):
				notes[i] = (note_pitch, note_time, length, velocity, muted)
				changed = True

		if changed:
			self._write_note_cache_to_clip(notes)

	def _set_length_for_pitch_at_step(self, idx, pitch, target_length):
		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		notes = list(self._note_cache)
		changed = False

		for i, note in enumerate(notes):
			# Unpack: (Pitch, Time, Duration, Velocity, Muted)
			note_pitch, note_time, note_duration, note_velocity, muted = note

			if (note_pitch == pitch and start_time <= note_time < end_time):
				# Only change Duration (index 2). Preserve Velocity (index 3).
				notes[i] = (note_pitch, note_time, target_length, note_velocity, muted)
				changed = True

		if changed:
			self._write_note_cache_to_clip(notes)
			# Don't forget to update cache reference too
			self._note_cache = tuple(notes)

	def _velocity_wait_tick(self):
		#self._control_surface.log_message(	"VELOCITY TICK anim=%s" % self._velocity_wait_animation)
		if not self._velocity_wait_animation:
			#self._control_surface.log_message("VELOCITY ANIMATION FINISHED")
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
	def set_display_octave(self, octave):
		self._display_octave = max(0, min(6, octave))
		# debug
		self._control_surface.log_message("DISPLAY OCTAVE = %s" % self._display_octave)

		self._parse_notes()
		self._force_update = True
		self.update()

	def _update_mode_notes_octaves_button(self):
		if self.is_enabled():
			if (self._mode_notes_octaves_button != None):
				if self._clip != None:
					self._mode_notes_octaves_button.set_on_off_values("StepSequencer2.Octave.On", "StepSequencer2.Octave.Dim")
					if self._mode == STEPSEQ_MODE_OCTAVE_OVERVIEW:
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
			# Check for Release Event (Momentary Button Release)
			if ((value == 0) and (sender.is_momentary())):
				self._is_octave_shifted = False  # Reset shift flag

				# CYCLE LOGIC
				current_mode = self._mode

				if current_mode == STEPSEQ_MODE_NOTES:
					# Transition: Notes -> Octave Edit
					new_mode = STEPSEQ_MODE_OCTAVE_OVERVIEW
					msg = "Octave Overview"
				elif current_mode == STEPSEQ_MODE_OCTAVE_OVERVIEW:
					# Transition: Overview -> Notes (Loop back to start)
					new_mode = STEPSEQ_MODE_NOTES
					msg = "Notes Mode"
				else:
					# Fallback if we are in some other weird state (e.g., velocity editor)
					new_mode = STEPSEQ_MODE_NOTES
					msg = "Notes Mode"

				# Only change mode if different
				if new_mode != current_mode:
					self.set_mode(new_mode)
					self._control_surface.show_message(msg)

				# CRITICAL: Update the parent OSD and force a matrix refresh
				if hasattr(self._step_sequencer, '_update_OSD'):
					self._step_sequencer._update_OSD()
				self.update()

			else:
				# Handle Button Hold (Shift Logic)
				# Keep existing behavior for holding the button
				self._is_octave_shifted = True
			# You might want to show a message here if needed
			# self._control_surface.show_message("Holding Octave Shift")

			# Always update the button LED state based on current mode
			self._update_mode_notes_octaves_button()

		else:
			# No clip selected
			if self._mode_notes_octaves_button:
				self._mode_notes_octaves_button.set_light("DefaultButton.Disabled")
# VELOCITIES
	def _set_velocity_at_step(self, idx, velocity_index):

		velocity = self._velocity_map[velocity_index]

		notes = list(self._note_cache)

		start_time = idx * self._resolution
		end_time = start_time + self._resolution

		changed = False

		for i, note in enumerate(notes):

			pitch, note_time, length, old_velocity, muted = note

			if start_time <= note_time < end_time:
				notes[i] = (
					pitch,
					note_time,
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
					# FORCE UPDATE TO REFRESH DISPLAY AFTER EXITING
					self._force_update = True
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
					self._control_surface.show_message("vertical velocty editing")
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

					self._control_surface.show_message("Select step or press Velocity button again")

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

			pitch, note_time, old_length, velocity, muted = note

			if start_time <= note_time < end_time:
				notes[i] = (
					pitch,
					note_time,
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

		if self.is_enabled():
			if (self._mode_notes_lengths_button != None):
				if self._clip != None:
					self._mode_notes_lengths_button.set_on_off_values("StepSequencer2.Length.On", "StepSequencer2.Length.Dim")
					# Only turn ON if currently ANIMATING.
					# In Horizontal/Vertical modes, it should be OFF (acting as an exit button).
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

		if self.is_enabled() and self._clip != None:
			# Handle Button Release (Momentary) -> Main Logic
			if ((value == 0) and (sender.is_momentary())):
				self._is_notes_lengths_shifted = False

				# --- STATE 1: Currently in Horizontal Length Editor -> Exit to Notes ---
				if self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
					self._length_wait_animation = False
					self._pending_length_editor = False
					self._editing_step = None
					self._is_length_editor_vertical = False
					self.set_mode(STEPSEQ_MODE_NOTES)
					self._control_surface.show_message("pitch")
					# Update button LED immediately
					self._mode_notes_lengths_button.turn_off()

				# --- STATE 2: Currently in Vertical Length Editor -> Exit to Notes ---
				elif self._mode == STEPSEQ_MODE_VERTICAL_LENGTH:
					self._length_wait_animation = False
					self._pending_length_editor = False
					self._editing_step = None
					self._is_length_editor_vertical = False
					self._force_update = True
					self.set_mode(STEPSEQ_MODE_NOTES)
					self._control_surface.show_message("pitch")
					self._mode_notes_lengths_button.turn_off()

				# --- STATE 3: Currently in Animation Mode -> Enter Vertical Length Mode ---
				# This is the missing piece from my previous attempt!
				elif self._length_wait_animation:
					# Stop animation immediately
					self._length_wait_animation = False
					# Cancel any pending horizontal entry
					self._pending_length_editor = False
					# Switch to Vertical Mode
					self.set_mode(STEPSEQ_MODE_VERTICAL_LENGTH)
					self._control_surface.show_message("Vertical Length editing")
					self.update()
					self._step_sequencer._update_OSD()
					# Keep LED ON because we are in a special mode?
					# Actually, in Velocity mode, the button stays ON during animation,
					# but turns OFF when entering Horizontal/Vertical editors usually,
					# unless the Vertical mode acts like a toggle.
					# Let's follow Velocity: Turn OFF for Vertical/Horizontal editors.
					self._mode_notes_lengths_button.turn_off()

				# --- STATE 4: In Normal Notes Mode -> Enter Animation Mode ---
				else:
					# Reset any previous editor states
					self._pending_length_editor = True
					self._editing_step = None
					self._is_length_editor_vertical = False

					# Start Animation
					self._length_wait_animation = True
					now = time.time()
					self._length_wait_start_times[0] = now
					for x in range(8):
						self._length_wait_start_times[x] = now + uniform(0.0, 0.3)

					# Force update to show animation immediately
					self._force_update = True
					# Schedule the animation loop
					self._control_surface.schedule_message(1, self._length_wait_tick)

					self._control_surface.show_message("Select step or press Length button again")

					# CRITICAL FIX: Update LED state based on animation status (The code you found)
					if self._length_wait_animation:
						self._mode_notes_lengths_button.turn_on()
					else:
						self._mode_notes_lengths_button.turn_off()

			# Handle Button Hold (Non-Momentary) -> Shift Logic
			else:
				self._is_notes_lengths_shifted = True
				self._is_mute_shifted = True
				# Sync global mute shift if applicable
				self._step_sequencer._is_mute_shifted = self._is_mute_shifted

			# Always refresh matrix and OSD
			self.update()
			self._step_sequencer._update_OSD()

		else:
			# Clip not selected, turn off button
			if self._mode_notes_lengths_button:
				self._mode_notes_lengths_button.set_light("DefaultButton.Disabled")



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

	# UTILS
	def uses_bottom_row(self):
		return self._mode in (
			STEPSEQ_MODE_STEP_VELOCITY_EDITOR,
			STEPSEQ_MODE_STEP_LENGTH_EDITOR,
			STEPSEQ_MODE_VERTICAL_VELOCITY,
			STEPSEQ_MODE_VERTICAL_LENGTH,
			STEPSEQ_MODE_COPY_PASTE,
			STEPSEQ_MODE_OCTAVE_OVERVIEW,
		)

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