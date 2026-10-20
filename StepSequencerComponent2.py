from _Framework.ControlSurfaceComponent import ControlSurfaceComponent
from _Framework.ButtonMatrixElement import ButtonMatrixElement
from _Framework.InputControlElement import InputControlElement, MIDI_CC_TYPE
from .StepSequencerComponent import StepSequencerComponent, ButtonElement
from .SequencerConstants import (RESOLUTION_NAMES, RESOLUTION_MAP,
	RESOLUTION_DOUBLE_TAP_WINDOW,
	RESOLUTION_DOUBLE_TAP_TICKS,
	RESOLUTION_LONG_PRESS,
	STEPSEQ_MODE_NOTES,
	STEPSEQ_MODE_NOTES_OCTAVES,
	STEPSEQ_MODE_NAV_OVERVIEW,
	STEPSEQ_MODE_COPY_PASTE,
	STEPSEQ_MODE_STEP_VELOCITY_EDITOR,
	STEPSEQ_MODE_STEP_LENGTH_EDITOR,
	STEPSEQ_MODE_VERTICAL_VELOCITY,
	STEPSEQ_MODE_VERTICAL_LENGTH,
	STEPSEQ_MODE_SCALE_EDIT,
	DRUM_BLOCK_SIZE,
	DRUM_BLOCK_COUNT,
	MELODIC_OCTAVE_COUNT,
	NAV_VISIBLE_BANDS,
	NAV_DOUBLE_TAP_WINDOW,
	NAV_DOUBLE_TAP_TICKS,
	NAV_BUTTON_DOUBLE_TAP_WINDOW,
	NAV_BUTTON_DOUBLE_TAP_TICKS,
	CLIP_GESTURE_TAP_WINDOW,
	CLIP_GESTURE_TAP_TICKS,
	LOOP_TAG_SYNC_DELAY_TICKS,
	SCALE_MODE_DELAY_TICKS,
	DRUM_COLORS_NO_NOTE,
	DRUM_COLORS_NOTE,
	DRUM_METRONOME_NO_NOTE,
	DRUM_METRONOME_NOTE,
	NOTE_VELOCITY_CC_CHANNEL,
	NOTE_VELOCITY_CC_NUMBER,
	DEFAULT_NOTE_VELOCITY,
	SLIDER_SUBDIVISIONS,
	VELOCITY_SUB_COLORS,
	LENGTH_SUB_COLORS,
	FOCUS_ROW,
	FOCUS_COLORS_IN_SCALE,
	FOCUS_COLORS_OUT_SCALE,
)
from .LoopSelectorComponent import (LoopSelectorComponent, apply_clip_loop,
                                    clamp_to_markers)
from .NoteSelectorComponent import NoteSelectorComponent
from .ScaleComponent import MUSICAL_MODES, KEY_NAMES
from .TrackControllerComponent import TrackControllerComponent
import time
from random import uniform
import json
from pathlib import Path
import hashlib
import os
import re
LONG_BUTTON_PRESS = 1.0
BASE_OCTAVE = 8
DEBUG_LOGGING = True

def _safe_bool(value):
    """Convert common string/int/bool representations safely."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ('true', '1', 'yes', 'on'):
            return True
        if value in ('false', '0', 'no', 'off', ''):
            return False
        raise ValueError(f"Invalid boolean value: {value!r}")
    return bool(value)
SUX_SCHEMA = [
    ('is_absolute',       False, _safe_bool),
    ('is_drumrack',       False, _safe_bool),
    ('display_octave',    2, int),
    ('resolution_index',  5, int),
    ('loop_block',        0, int),
    ('loop_page_offset',  0, int),
    ('clip_loop_start',   0.0, float),
    ('clip_loop_end',     16.0, float),
]
# The clip start/end markers are NOT stored: Live keeps them in the clip
# and the step sequencer never changes them. Extra values in a tag (e.g.
# markers written by an earlier version) are ignored.
SUX_PARAM_COUNT = len(SUX_SCHEMA)
METADATA_PREFIX = "[SUX:"
METADATA_SUFFIX = "]"
# Spaces between the clip name and the tag, so the tag is pushed out of
# the visible part of the clip name in Session View.
TAG_SEPARATOR = " " * 10

class ClipMetadataManager:
	"""Manages persistent clip metadata storage in JSON."""

	def __init__(self, control_surface):
		"""Initialize - ADD EXTREME LOGGING."""
		self.control_surface = control_surface
		self.cache = {"clips": {}}

		# Determine cache path with diagnostics
		self._cross_platform_cache_path_detect()
		self._pending_renames = []
		self._clip_refs = {}
		self._undo_listener_registered = False
		self._renamed_clips = {}
		self._backup_and_reset_cache()
		self._load_cache()

	def _cross_platform_cache_path_detect(self):
		user_home = Path.home()

		# Detect operating system
		import sys
		is_windows = sys.platform.startswith('win')
		is_mac = sys.platform == 'darwin'
		candidate_paths = []
		if is_mac:
			candidate_paths.append(user_home / "Documents" / "Ableton")
			candidate_paths.append(user_home / "Music" / "Ableton")
			candidate_paths.append(user_home / "Library" / "Application Support" / "Ableton")
		elif is_windows:
			# Windows conventions
			candidate_paths.append(user_home / "Documents" / "Ableton")
			candidate_paths.append(Path(os.environ.get('USERPROFILE', '')) / "Documents" / "Ableton")
		else:
			# Linux and others
			candidate_paths.append(user_home / "Documents" / "Ableton")
			candidate_paths.append(user_home / ".Ableton")

		# Test each candidate path for writability
		self.cache_path = None
		for test_path in candidate_paths:
			try:
				# Create directory if it doesn't exist
				test_path.mkdir(parents=True, exist_ok=True)

				# Test if writable
				test_file = test_path / ".path_writable_test.tmp"
				test_file.touch()
				test_file.unlink()

				# Success! Use this path
				self.cache_path = test_path / "launchpad_clip_metadata.json"
				break
			except Exception as path_err:
				if DEBUG_LOGGING:
					self._log(f"✗ Path not usable: {test_path} ({path_err})")
				continue

		# Fallback if no path worked
		if self.cache_path is None:
			import tempfile
			temp_dir = Path(tempfile.gettempdir())
			ableton_temp = temp_dir / "Ableton_Launchpad_Metadata"
			ableton_temp.mkdir(parents=True, exist_ok=True)
			self.cache_path = ableton_temp / "launchpad_clip_metadata.json"

	def _backup_and_reset_cache(self):
		"""Backup existing cache and start fresh for new project (cross-platform)."""
		if not self.cache_path or not self.cache_path.exists():
			return
		try:
			import shutil
			backup_path = self.cache_path.parent / f"launchpad_clip_metadata_BACKUP.json"

			# Copy to backup
			shutil.copy2(str(self.cache_path), str(backup_path))
			self.cache_path.unlink()
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._log(f"❌ BACKUP/RESET ERROR: {e}")
				self._log(traceback.format_exc())

	def ensure_clip_in_cache(self, clip):
		"""
		LAZY LOADING: Ensures a specific clip is in cache by extracting SUX tag.
		Call this when a clip is selected or modified.
		Returns True if clip was added/updated, False if no tag found.
		"""
		if not clip or not self.cache_path:
			return False
		try:
			# Get position
			track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)
			if track_idx < 0 or slot_idx < 0:
				return False

			# Extract tag from clip name
			clip_name = getattr(clip, 'name', '')
			params = self.extract_sux_params_from_name(clip_name)
			if not params:
				return False

			# Build cache entry
			content_hash = self._hash_clip_content(clip)
			key_pos = f"POS_{track_idx}_{slot_idx}"
			entry = {
				"settings": params,
				"track_index": track_idx,
				"slot_index": slot_idx,
				"content_hash": content_hash,
				"updated_at": time.time()
			}
			self.cache.setdefault("clips", {})
			self.cache["clips"][key_pos] = entry
			self._save_cache()
			return True
		except Exception as e:
			self._log(f"[ENSURE_CACHE_ERROR] {e}")
			return False

	def scan_all_clips(self):
		"""
		MANUAL FULL SCAN - Call this when user wants to rebuild entire cache.
		Use sparingly on large projects.
		Returns (total_scanned, tags_found) tuple.
		"""
		song = self.control_surface.song()
		scanned_count = 0
		found_tags = 0
		for track_idx, track in enumerate(song.tracks):
			for slot_idx, slot in enumerate(track.clip_slots):
				if slot.has_clip:
					clip = slot.clip
					clip_name = getattr(clip, 'name', '')
					params = self.extract_sux_params_from_name(clip_name)
					if params:
						found_tags += 1

						# Add to cache (same logic as ensure_clip_in_cache)
						content_hash = self._hash_clip_content(clip)
						key_pos = f"POS_{track_idx}_{slot_idx}"
						entry = {
							"settings": params,
							"track_index": track_idx,
							"slot_index": slot_idx,
							"content_hash": content_hash,
							"updated_at": time.time()
						}
						self.cache.setdefault("clips", {})
						self.cache["clips"][key_pos] = entry
					scanned_count += 1
		self._save_cache()
		return scanned_count, found_tags

	def _log(self, msg):
		"""Safe logging."""
		if hasattr(self, 'control_surface') and self.control_surface:
			try:
				self.control_surface.log_message(f"[META] {msg}")
			except RuntimeError:
				pass

	def _load_cache(self):
		"""Load JSON cache."""
		try:
			if self.cache_path.exists():
				with open(self.cache_path, 'r') as f:
					self.cache = json.load(f)
		except Exception as e:
			self._log(f"❌ Load failed: {e}")
			self.cache = {"clips": {}}

	def _save_cache(self):
		"""SAVE CACHE - WITH MAXIMUM DIAGNOSTICS."""
		try:
			self.cache_path.parent.mkdir(parents=True, exist_ok=True)
			entry_count = len(self.cache.get('clips', {}))
			with open(self.cache_path, 'w') as f:
				json.dump(self.cache, f, indent=2)
		except Exception as e:
			import traceback
			err_detail = f"❌ Save FAILED: {type(e).__name__}: {e}"
			self._log(err_detail)
			self._log(f"Traceback:\n{traceback.format_exc()}")

	def save_clip_to_json(self, clip, params_dict):
		"""Save clip settings with maximum diagnostics."""
		if not clip:
			return False
		try:
			track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)
			settings_only = {}
			settings_keys = [name for name, _, _ in SUX_SCHEMA] + ['timestamp', 'resolution_name']
			for key in settings_keys:
				if key in params_dict:
					settings_only[key] = params_dict[key]
			content_hash = self._hash_clip_content(clip)
			key_pos = f"POS_{track_idx}_{slot_idx}"
			entry = {
				"settings": settings_only,
				"track_index": track_idx,
				"slot_index": slot_idx,
				"content_hash": content_hash,
				"updated_at": time.time()
			}
			self.cache.setdefault("clips", {})
			self.cache["clips"][key_pos] = entry
			self._save_cache()
			return True
		except Exception as e:
			import traceback
			self._log(f"Save error: {e}")
			self._log(traceback.format_exc())
			return False

	def _hash_clip_content(self, clip):
		"""Hash clip content."""
		try:
			note_sig = ""
			if hasattr(clip, 'notes'):
				try:
					notes = list(clip.notes)[:64]
					for n in notes:
						note_sig += f"{n.pitch}_{n.start_time}_{n.duration}_"
					if note_sig:
						return hashlib.md5(note_sig.encode()).hexdigest()[:32]
				except:
					pass
			track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)
			if track_idx >= 0 and slot_idx >= 0:
				pos_str = f"T{track_idx}_S{slot_idx}"
				full_hash = pos_str.encode('utf-8').hex()
				return full_hash
			else:
				return f"ERR_{id(clip):08x}"
		except Exception as e:
			self._log(f"hash error: {e}")
			return f"ERR_{id(clip):08x}"

	def _get_current_track_slot_indices_safe(self, clip):
		"""Get indices safely."""
		try:
			if not clip:
				return -1, -1
			song = self.control_surface.song()
			if not song:
				return -1, -1
			for t_idx, track in enumerate(song.tracks):
				for s_idx, slot in enumerate(track.clip_slots):
					if slot.has_clip and slot.clip == clip:
						return t_idx, s_idx
			return -1, -1
		except Exception as e:
			self._log(f"index error: {e}")
			return -1, -1

	def recover_clip_settings_from_json(self, clip):
		"""Recover settings from JSON."""
		if not clip:
			return None
		try:
			track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)
			if track_idx < 0 or slot_idx < 0:
				return None
			return None
		except Exception as e:
			self._log(f"recovery error: {e}")
			return None

	def extract_sux_params_from_name(self, clip_name):
		"""
        Extract and validate the first SUX metadata tag.
        Canonical format:
            [SUX:{is_absolute;is_drumrack;octave;resolution;
                  loop_block;page_offset;loop_start;loop_end}]
        Boolean fields accept both:
            0 / 1
            False / True
        """
		if not clip_name:
			return None
		try:
			start_idx = clip_name.find(METADATA_PREFIX)
			if start_idx == -1:
				return None
			end_idx = clip_name.find(
				METADATA_SUFFIX,
				start_idx + len(METADATA_PREFIX)
			)
			if end_idx == -1:
				return None

			# Extract:
			# [SUX:{ ... }]
			#
			# Start immediately after "[SUX:{"
			param_start = start_idx + len(METADATA_PREFIX) + 1
			param_string = clip_name[param_start:end_idx].strip()

			# Remove closing }
			if param_string.endswith("}"):
				param_string = param_string[:-1].strip()
			if not param_string:
				return None
			values = [value.strip() for value in param_string.split(";")]
			if len(values) < SUX_PARAM_COUNT:
				return None
			params = {}
			for i, (param_name, default_value, converter) in enumerate(SUX_SCHEMA):
				raw_value = values[i]
				if raw_value == "":
					params[param_name] = default_value
				else:
					params[param_name] = converter(raw_value)
			return params
		except Exception as e:
			if DEBUG_LOGGING:
				self._log(f"[TAG_PARSE_ERROR] {e}")
			return None

	# Accessiblity for backward compatibility
	def extract_embedded_parameters(self, clip_name):
		"""Alias for compatibility with existing MelodicNoteEditorComponent calls."""
		return self.extract_sux_params_from_name(clip_name)

	def _register_undo_listener(self):
		"""Register to listen for undo stack changes."""
		if self._undo_listener_registered:
			return
		try:
			song = self.control_surface.song()
			song.add_undo_stack_change_listener(self._on_undo_state_changed)
			self._undo_listener_registered = True
		except Exception as e:
			self._log(f"Undo listener registration failed: {e}")

	def _unregister_undo_listener(self):
		"""Clean up undo listener on disconnect."""
		if not self._undo_listener_registered:
			return
		try:
			song = self.control_surface.song()
			song.remove_undo_stack_change_listener(self._on_undo_state_changed)
			self._undo_listener_registered = False
		except:
			pass

	def _store_clip_reference(self, object_id, clip):
		"""Store weak reference to clip for later retrieval."""
		import weakref
		try:
			weak_ref = weakref.ref(clip)
			self._renamed_clips[str(object_id)] = weak_ref
		except TypeError:
			self._renamed_clips[str(object_id)] = None

	def _get_clip_by_object_id(self, object_id):
		"""Retrieve clip from stored weak reference."""
		ref = self._renamed_clips.get(str(object_id))
		if ref is None:
			return None
		elif callable(ref):
			return ref()
		else:
			return None

	def _on_undo_state_changed(self):
		"""Fires after every Live operation - process pending renames."""
		self._try_process_pending_operations()

	def _try_process_pending_operations(self):
		"""Attempt to apply pending renames now that we're between Live callbacks."""
		processed = []
		for i, pending_op in reversed(list(enumerate(self._pending_renames))):
			clip_id, target_name, clip_object_id = pending_op
			try:
				clip_obj = self._get_clip_by_object_id(clip_object_id)
				if not clip_obj or not hasattr(clip_obj, 'name'):
					processed.append(i)
					continue
				try:
					clip_obj.name = target_name
					processed.append(i)
				except RuntimeError as re:
					self._log(f"Still blocked: {re}")
			except Exception as e:
				self._log(f"Rename error: {e}")
				processed.append(i)
		for idx in sorted(processed, reverse=True):
			del self._pending_renames[idx]
		self._cleanup_stale_clip_references()

	def _cleanup_stale_clip_references(self):
		"""Remove expired weak references."""
		stale_keys = []
		for key, ref in self._renamed_clips.items():
			if ref is not None and callable(ref):
				if ref() is None:
					stale_keys.append(key)
		for key in stale_keys:
			del self._renamed_clips[key]

	def remove_clip_from_json_by_position(self, track_idx, slot_idx):
		"""Remove the JSON metadata entry for an emptied clip slot."""
		try:
			if track_idx is None or slot_idx is None:
				return False
			if track_idx < 0 or slot_idx < 0:
				return False
			key_pos = f"POS_{track_idx}_{slot_idx}"
			clips = self.cache.setdefault("clips", {})
			if key_pos not in clips:
				return False
			del clips[key_pos]
			self._save_cache()
			return True
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._log(
					f"[JSON_CLEANUP_ERROR] {e}\n"
					f"{traceback.format_exc()}"
				)
			return False

class MelodicNoteEditorComponent(ControlSurfaceComponent):
	def __init__(self, step_sequencer, matrix, side_buttons, control_surface):
		self._initializing = True
		ControlSurfaceComponent.__init__(self)
		self._control_surface = control_surface
		self.set_enabled(False)
		# ---------------------------------------------------------
		# METADATA MANAGER SETUP - WITH ENHANCED ERROR HANDLING
		# ---------------------------------------------------------
		self._meta_manager = None
		try:
			# CRITICAL: Pass control_surface so manager can log
			self._meta_manager = ClipMetadataManager(control_surface)
			self._setup_observer_listeners()
		except Exception as e:
			import traceback
			self._control_surface.log_message(f"[META_INIT_ERROR] {e}\n{traceback.format_exc()}")
			self._meta_manager = None
		self._step_sequencer = step_sequencer

		# this needs to be right after self._step_sequencer = step_sequencer
		self._clip_slot = None
		self._matrix = matrix
		self._side_buttons = side_buttons
		self.set_matrix(matrix)
		self._grid_buffer = [[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0],
		[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0],
		[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0]]
		self._grid_back_buffer = [[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0],
		[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0],
		[0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0]]

		# clip (rest)
		self._loading_clip = False
		self._clip = None
		self._note_cache = []
		self._force_update = True
		self.song().view.add_selected_track_listener(self._on_selected_track_changed)
		self.song().view.add_selected_scene_listener(self._on_selected_scene_changed)
		self._register_clip_slot_listener()
		self._init_data()
		self._velocity_map = [0, 18, 36, 54, 72, 90, 108,127]
		# Velocity of new notes: last value of the velocity CC (SequencerConstants)
		self._note_velocity = DEFAULT_NOTE_VELOCITY
		self._velocity_cc = InputControlElement(
			MIDI_CC_TYPE, NOTE_VELOCITY_CC_CHANNEL, NOTE_VELOCITY_CC_NUMBER)
		self._velocity_cc.add_value_listener(self._on_velocity_cc_value)
		self._length_map = [0.5, 1, 2, 3, 4, 8, 16, 32]

		# time
		self._playhead = 0
		self._page = 0

		# notes
		self._key_indexes = [36, 37, 38, 39, 40, 41, 42, 43]
		self._key_index_is_in_scale = [True, False, True, True, False, True, False, True]
		self._key_index_is_root_note = [True, False, False, False, False, False, False, False]
		self._is_monophonic = False
		self._is_drumrack_layout = False
		# Focus mode (melodic): pitch on LP row FOCUS_ROW, or None (see set_focus_pitch)
		self._focus_pitch = None

		# octaves
		# NOTE: in drumrack layout _display_octave holds the PITCH BLOCK
		# index (0..15, 8 chromatic notes each) instead of an octave.
		self._display_octave = 2
		self._overview_start_octave = 2
		self._last_displayed_octave = 2
		self._scroll_pending_action = False
		self._resolution = 16
		# MODE
		self._mode = STEPSEQ_MODE_NOTES

		# Navigation overview: buttons currently held (press order) and
		# whether a two-button loop selection has been performed.
		self._nav_held = []
		self._nav_combo_done = False
		# Single tap waiting to see whether it becomes a double tap:
		# (x, y, release_time) or None
		self._nav_pending_tap = None

		# Clip on/off
		self._clip_toggle_button = None
		self.set_clip_toggle_button(self._side_buttons[1])

		# Copy/paste/delete
		self._mode_copy_paste_button = None
		self.set_mode_copy_paste_button(self._side_buttons[2])
		self._copied_grid_data = None
		self._paste_armed = False
		self._last_press_time = 0.0
		self._suppress_toggle_release = False
		# Taps within the double-tap window: 1 = copy/paste, 2 = erase (or undo
		# on an empty grid), 3 = cut (turns the erase just done into a cut)
		self._copy_tap_count = 0
		self._delete_action_on_release = False
		self._cut_action_on_release = False
		# Notes removed by the last erase (same format as _copied_grid_data),
		# restored by double-tapping on an empty grid
		self._erased_grid_data = None
		# True when the erase of the current tap sequence actually removed notes
		self._erase_just_done = False
		self._skip_copy_release = False
		self._duplicate_pending = False
		# Toggle held + Copy taps: 1 tap = duplicate clip (deferred until the
		# double-tap window has passed), 2 taps = delete clip / restore it
		self._clip_gesture_active = False
		self._clip_gesture_count = 0
		self._clip_gesture_last_release = 0.0
		# Release time of a single tap whose duplicate has not run yet
		self._pending_duplicate_time = None
		# Snapshot of the last deleted clip, restored into an empty slot
		self._deleted_clip_data = None
		self._update_copy_paste_button_state()

		# Length mode
		self._mode_notes_lengths_button = None
		self.set_mode_notes_lengths_button(self._side_buttons[3])
		self._is_notes_lengths_shifted = False
		self._last_notes_lengths_button_press = time.time()
		self._mode_notes_velocities_button = None

		# Velocity mode
		self.set_mode_notes_velocities_button(self._side_buttons[4])
		self._is_notes_velocity_shifted = False
		self._last_notes_velocity_button_press = time.time()
		self._mode_notes_octaves_button = None

		# Octave mode
		self.set_mode_notes_octaves_button(self._side_buttons[6])
		self._is_octave_shifted = False
		self._last_notes_octaves_button_press = time.time()
		self._is_velocity_shifted = False
		self._is_mute_shifted = False

		# loop selector
		self._loop_block = 0  # Track current loop block independently

		# disable the lock but allow the code to be used in the future if desired
		self._is_locked = False  # Add this line
		self._lock_to_track = False  # Add this line

		# Final initialization of button state
		if DEBUG_LOGGING:
			self._control_surface.log_message("[INIT] Completing button setup")
		self._update_copy_paste_button_state()
		if hasattr(self, '_force_update'):
			self._force_update = True
		self._sel_seq = 0
		self._initializing = False
		if DEBUG_LOGGING:
			self._control_surface.log_message("[INIT] Initial state complete")

	def disconnect(self):
		"""
		Clean up resources and FLUSH ALL PENDING RENAMES before exit.
		CRITICAL: This ensures SUX tags get written to clips even if
		they were queued and never executed due to lack of undo activity.
		"""
		if self._meta_manager and self._clip:
			try:
				old_state = self._get_current_state_dict()
				self._meta_manager.save_clip_to_json(self._clip, old_state)
			except Exception as e:
				self._control_surface.log_message(f"[PREV_CLIP_SAVE_ERROR] {e}")
		self._flush_pending_renames()
		self._remove_observer_listeners()
		if hasattr(self, '_meta_manager') and self._meta_manager:
			self._meta_manager._unregister_undo_listener()
		self._remove_highlighted_clip_slot_listener()
		self._remove_clip_slot_listener()
		self._step_sequencer = None
		if self._matrix != None:
			for x in range(8):
				for y in range(8):
					button = self._matrix.get_button(x, y)
					try:
						button.remove_value_listener(self._matrix_value)
					except RuntimeError:
						pass
		self._matrix = None
		if self._velocity_cc is not None:
			self._velocity_cc.remove_value_listener(self._on_velocity_cc_value)
			self._velocity_cc = None
		self._mode_notes_lengths_button = None
		self._mode_notes_octaves_button = None
		self._mode_notes_velocities_button = None
		self._mode_notes_pitches_button = None
		self._notes_pitches = None
		self._notes_velocities = None
		self._notes_octaves = None
		self._notes_lengths = None
		self._clip = None

		# Finalize meta manager
		self._meta_manager = None

	@property
	def resolution_beats(self):
		"""SAFE accessor - always returns valid beat value ≥ 0.001."""
		val = getattr(self, '_resolution', 0.25)
		if isinstance(val, (int, float)):
			if val <= 0:
				return 0.25
			if val >= 0 and val < 8 and isinstance(val, int):
				from .SequencerConstants import RESOLUTION_MAP
				try:
					converted = float(RESOLUTION_MAP[val])
					if converted > 0:
						return converted
				except:
					pass
			return max(0.001, val)

		# Unknown type - safest fallback
		return 0.25

	def extract_embedded_parameters(self, clip_name):
		"""
		Parse SUX tag from clip name. Extracts from FIRST valid tag only.
		Handles BOTH:
		- Good format: [SUX:{0;3;3;0;0;0.0;24.0}]
		Returns None if no valid tag found.
		"""
		if not clip_name:
			return None
		try:
			# Find FIRST tag start: [SUX:
			start_idx = clip_name.find(METADATA_PREFIX)
			if start_idx == -1:
				return None

			# Find NEXT closing bracket ] (not rfind, use find to get first match)
			end_idx = clip_name.find(METADATA_SUFFIX, start_idx + len(METADATA_PREFIX))
			if end_idx <= start_idx:
				return None

			# Extract content between FIRST [SUX: and FIRST ]
			param_start = start_idx + len(METADATA_PREFIX) + 1
			param_string = clip_name[param_start:end_idx].strip()
			if param_string.endswith('}'):
				param_string = param_string[:-1]
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[EXTRACT_DEBUG] Extracted from FIRST tag: '{param_string[:40]}...'"
				)

			# Must have semicolons
			if ";" not in param_string:
				return None
			values = param_string.split(";")
			if len(values) < 7:
				return None
			params = {}
			for i, (param_name, default_value, converter) in enumerate(SUX_SCHEMA):
				raw_value = values[i].strip() if i < len(values) else ""
				params[param_name] = converter(raw_value) if raw_value else default_value
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[TAG_OK] Oct={params['display_octave']}, Res={params['resolution_index']}"
				)
			return params
		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[TAG_ERROR] {e}")
			return None

	def build_embedded_parameters_tag(self, params_dict):
		"""
		Construct the SUX metadata tag from parameter dictionary.
		Format: [SUX:{is_absolute;is_drumrack;oct;res;blk;off;start;end}]
		CRITICAL: Must include BOTH opening '[' and closing ']' AND curly braces '{' '}'!
		Returns:
			str: Formatted tag like "[SUX:{0;0;2;4;0;0;0.0;16.0}]"
		"""
		try:
			values = []
			for param_name, default_value, converter in SUX_SCHEMA:
				value = params_dict.get(param_name, default_value)
				if converter == _safe_bool:
					values.append("1" if bool(value) else "0")
				else:
					values.append(str(converter(value)))
			param_string = ";".join(values)
			full_tag = f"{METADATA_PREFIX}{{{param_string}}}{METADATA_SUFFIX}"
			return full_tag
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._control_surface.log_message(f"[BUILD_TAG_ERROR] {e}\n{traceback.format_exc()}")
			# Fallback with schema defaults
			default_values = []
			for _, default_value, _ in SUX_SCHEMA:
				if isinstance(default_value, bool):
					default_values.append("0")
				elif isinstance(default_value, float):
					default_values.append(str(round(default_value, 1)))
				else:
					default_values.append(str(int(default_value)))
			return f"{METADATA_PREFIX}{{{';'.join(default_values)}}}{METADATA_SUFFIX}"

	def update_clip_name_with_params(self, clip, params_dict):
		"""
		Update clip name with SUX parameter tag.
		STRATEGY:
		1. CHECK if valid tag already exists with matching parameters
		2. If not, attempt IMMEDIATE write first
		3. If Live blocks it, use schedule_message() for proper deferral
		4. Still maintain backup queue for disconnect flush
		Returns True if tag was successfully placed/verified/queued, False on critical failure.
		"""
		if not clip or not hasattr(clip, 'name'):
			if DEBUG_LOGGING:
				self._control_surface.log_message("[NAME_UPDATE] No clip/name available")
			return False
		try:
			original_name = getattr(clip, 'name', '')
			tag_count = original_name.count(METADATA_PREFIX)
			if tag_count > 1:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[DUPLICATE_DETECTED] Found {tag_count} tags in clip name! " +
						f"Stripping all..."
					)
				clean_before = self.strip_metadata_tags(original_name)
				if clean_before != original_name:
					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[CLEANUP_DONE] Stripped duplicates, now: '{clean_before[:50]}...'"
						)

			# Build the desired tag
			new_tag = self.build_embedded_parameters_tag(params_dict)

			# Verify tag format before proceeding
			if not new_tag.startswith(METADATA_PREFIX):
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_BUILD_FAIL] Tag doesn't start with prefix: '{new_tag}'"
					)
				return False

			# Ensure it ends with suffix
			if not new_tag.endswith(METADATA_SUFFIX):
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_BUILD_WARN] Tag missing closing bracket, auto-correcting"
					)
				new_tag = new_tag + METADATA_SUFFIX
			if not new_tag.startswith(METADATA_PREFIX):
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_BUILD_FAIL] Tag doesn't start with '[SUX:': '{new_tag}'"
					)
				return False
			if not new_tag.endswith(METADATA_SUFFIX):
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_BUILD_WARN] Tag missing closing bracket, auto-correcting"
					)
				# Auto-fix by appending missing bracket
				new_tag = new_tag + METADATA_SUFFIX
			original_name = getattr(clip, 'name', '')
			clean_name = self.strip_metadata_tags(original_name)
			need_update = True
			tag_exists_and_valid = False
			extract_from_old = None
			if METADATA_PREFIX in original_name and "}" in original_name and METADATA_SUFFIX in original_name:
				start_bracket = original_name.find(METADATA_PREFIX)
				end_bracket = original_name.rfind(METADATA_SUFFIX)
				if end_bracket > start_bracket:
					potential_tag = original_name[start_bracket:end_bracket + 1]
					if "{" in potential_tag and ";" in potential_tag:
						extract_from_old = self._meta_manager.extract_embedded_parameters(original_name)
						if extract_from_old:
							tag_exists_and_valid = True
							keys_to_check = [param_name for param_name, _, _ in SUX_SCHEMA]
							all_match = True
							for key in keys_to_check:
								old_val = extract_from_old.get(key)
								new_val = params_dict.get(key)
								if old_val != new_val:
									all_match = False
							if all_match:
								need_update = False
							else:
								if DEBUG_LOGGING:
									self._control_surface.log_message(
										f"[TAG_PARAMS_MISMATCH] Tags differ, must update"
									)
					elif not extract_from_old:
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								f"[TAG_CORRUPT] Existing tag malformed, will rewrite"
							)
			if clean_name:
				new_full_name = f"{clean_name}{TAG_SEPARATOR}{new_tag}"
			else:
				new_full_name = f"{TAG_SEPARATOR}{new_tag}"
			# Same values but older spacing before the tag -> still rename.
			# (Compared stripped too, in case Live trims a leading space.)
			if not need_update and (
					original_name == new_full_name
					or original_name.strip() == new_full_name.strip()):
				return True
			immediate_success = False
			try:
				clip.name = new_full_name
				immediate_success = True
				actual_name = getattr(clip, 'name', '')
				if new_tag in actual_name:
					return True
				elif METADATA_PREFIX in actual_name and METADATA_SUFFIX in actual_name:
					return True
				else:
					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[TAG_VERIFY_FAIL] ✗ Write succeeded but tag MISSING!"
						)
			except RuntimeError as re:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_IMMEDIATE_FAILED] Immediate write blocked: {re}. " +
						f"Attempting scheduled deferral..."
					)
			if not immediate_success:
				rename_request = {
					'clip_object': clip,
					'target_name': new_full_name,
					'timestamp': time.time(),
					'attempt_count': 1
				}

				# Store temporarily for scheduled callback access
				self._pending_deferred_rename = rename_request

				# Schedule on next frame (after callback chain completes)
				if hasattr(self._control_surface, 'schedule_message'):
					try:
						self._control_surface.schedule_message(1, self._execute_deferred_rename)
						if DEBUG_LOGGING:
							display_queued = new_full_name[:40] + ('...' if len(new_full_name) > 40 else '')
							self._control_surface.log_message(
								f"[TAG_SCHEDULED] Tag will be written on next frame: '{display_queued}'"
							)
						return True
					except Exception as sch_err:
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								f"[TAG_SCHED_ERR] Failed to schedule: {sch_err}. " +
								f"Falling back to queue..."
							)

				# FINAL FALLBACK: Add to traditional queue (less reliable)
				if hasattr(self, '_meta_manager') and self._meta_manager:
					clip_object_id = id(clip)
					self._meta_manager._pending_renames.append((None, new_full_name, clip_object_id))
					self._meta_manager._store_clip_reference(clip_object_id, clip)
					if DEBUG_LOGGING:
						display_backup = new_full_name[:40] + ('...' if len(new_full_name) > 40 else '')
						self._control_surface.log_message(
							f"[TAG_BACKUP_QUEUE] ⚠️ Tag queued (backup strategy)"
						)
					return True
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_FINAL_FAIL] Cannot defer rename, meta manager missing"
					)
				return False  # Complete failure

			# This line should never reach if above logic works correctly
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[TAG_UNEXPECTED_PATH] Unexpected code path reached")
			return False

		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._control_surface.log_message(f"[NAME_QUEUE_ERROR] ❌ {e}\n{traceback.format_exc()}")
			return False

	def _execute_deferred_rename(self):
		"""Callback that executes previously scheduled rename."""
		if not hasattr(self, '_pending_deferred_rename') or not self._pending_deferred_rename:
			return
		rename_request = self._pending_deferred_rename
		clip = rename_request['clip_object']
		target_name = rename_request['target_name']

		# Clear the stored reference
		delattr(self, '_pending_deferred_rename')
		if not clip or not hasattr(clip, 'name'):
			return

		# Try to write now - callback chain should be complete
		try:
			clip.name = target_name

			# Verify
			actual_name = getattr(clip, 'name', '')
			if METADATA_PREFIX in actual_name and METADATA_SUFFIX in actual_name:
				return
		except RuntimeError as re:
			# Still blocked - fall back to traditional queue
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[DEFERRED_BLOCKED] Still blocked after delay: {re}. Queuing permanently..."
				)

			# Move to persistent queue for disconnect flush
			if hasattr(self, '_meta_manager') and self._meta_manager:
				clip_object_id = id(clip)
				self._meta_manager._pending_renames.append((None, target_name, clip_object_id))
				self._meta_manager._store_clip_reference(clip_object_id, clip)
		return

	def sync_clip_with_json(self):
		"""
		CRITICAL: Call this EVERY TIME parameters change!
		Separates SETTINGS from POSITION to avoid polluting settings dict.
		CRITICAL FIX: Read loop_block from LoopSelector (LATEST value), NOT from self._loop_block!
		"""
		if not self._clip or not self._meta_manager:
			return
		try:
			# 1. Get CURRENT STATE (SETTINGS ONLY - no position!)
			settings_only = self._get_current_state_dict()

			# 2. ⭐⭐⭐ CRITICAL: READ loop_block FROM LOOP SELECTOR (ALWAYS LATEST) ⭐⭐⭐
			# DO NOT trust self._loop_block - read directly from source of truth!
			if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
				ls = self._step_sequencer._loop_selector
				if hasattr(ls, '_block'):
					latest_block = ls._block
					settings_only['loop_block'] = latest_block
				else:
					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[SYNC_LOOP_BLOCK_WARN] LoopSelector missing _block attribute!"
						)
			else:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[SYNC_LOOP_BLOCK_WARN] No LoopSelector available!"
					)

			# 3. Get POSITION SEPARATELY (not stored in settings dict!)
			track_idx, slot_idx = self._meta_manager._get_current_track_slot_indices_safe(self._clip)
			save_payload = {
				**settings_only,
				'track_index': track_idx,
				'slot_index': slot_idx
			}

			# 5. SAVE TO JSON
			self._meta_manager.save_clip_to_json(self._clip, save_payload)

			# 6. UPDATE TAG IN CLIP NAME (only save settings, not position!)
			# Make sure we pass the same settings_only (with corrected loop_block)
			self.update_clip_name_with_params(self._clip, settings_only)
		except Exception as e:
			import traceback
			self._control_surface.log_message(f"[SYNC_ERROR] {e}\n{traceback.format_exc()}")

	def strip_metadata_tags(self, clip_name):
		"""
		Remove ALL SUX tags from clip name (not just the last one).
		Handles any number of duplicate tags:
		- Single: [SUX:{0;0;2;4;0;0;0.0;16.0}]
		- Multiple: [SUX:{...}] [SUX:{...}] [SUX:{...}]
		- Corrupt: [SUX:...] without {}
		ALWAYS returns string (never None).
		Uses METADATA_PREFIX and METADATA_SUFFIX constants.
		"""
		if not clip_name:
			return ""

		# Escape constants for regex safety
		prefix_esc = re.escape(METADATA_PREFIX)
		suffix_esc = re.escape(METADATA_SUFFIX)

		# Pattern matches ONE tag: [SUX:...] where ... is anything until next ]
		# Use GLOBAL flag (flags=re.UNICODE or re.MULTILINE) to replace ALL occurrences
		pattern_all = rf'{prefix_esc}[^\]]*{suffix_esc}'

		# CRITICAL FIX: Use re.sub with GLOBAL replacement (replace ALL tags, not just last)
		# This removes EVERY tag anywhere in the string
		cleaned = re.sub(pattern_all, '', clip_name).strip()

		# SAFETY: Ensure we return a valid string (never None)
		if cleaned is None:
			return clip_name
		return cleaned

	def _verify_final_tag(self, clip, expected_state):
		"""Delayed verification that runs after deferred writes complete."""
		if not clip or not hasattr(clip, 'name'):
			return
		try:
			expected_tag = self.build_embedded_parameters_tag(expected_state)
			actual_name = getattr(clip, 'name', '')
			expected_params_str = expected_tag.replace(METADATA_PREFIX, "").replace(METADATA_SUFFIX, "")
			has_correct_params = expected_params_str in actual_name or \
								 expected_tag in actual_name or \
								 expected_params_str.replace(";", ";") in actual_name
			if not has_correct_params:
				extracted = self._meta_manager.extract_embedded_parameters(actual_name)
				if extracted:
					all_match = all(
						extracted.get(param_name) == expected_state.get(param_name)
						for param_name, _, _ in SUX_SCHEMA
					)
					has_correct_params = all_match
				if not has_correct_params:
					# Check if ANY tag exists
					if METADATA_PREFIX in actual_name and METADATA_SUFFIX in actual_name:
						start_idx = actual_name.find(METADATA_PREFIX)
						end_idx = actual_name.rfind(METADATA_SUFFIX) + 1
						existing_tag = actual_name[start_idx:end_idx]
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._control_surface.log_message(f"[VERIFY_ERROR] {e}\n{traceback.format_exc()}")

	def _flush_pending_renames(self):
		"""
		FORCE-FLUSH all pending rename operations immediately.
		Call this during disconnect() before script shutdown to ensure
		any queued tags get written to clip names before Live exits.
		Returns count of successfully processed renames.
		"""
		if not hasattr(self, '_meta_manager') or not self._meta_manager:
			return 0
		if not self._meta_manager._pending_renames:
			return 0
		processed_count = 0
		skipped_invalid = 0
		for i in reversed(list(range(len(self._meta_manager._pending_renames)))):
			pending_op = self._meta_manager._pending_renames[i]
			clip_id, target_name, clip_object_id = pending_op
			clip_obj = self._meta_manager._get_clip_by_object_id(clip_object_id)
			if not clip_obj or not hasattr(clip_obj, 'name'):
				del self._meta_manager._pending_renames[i]
				skipped_invalid += 1
				continue

			# Try to force rename now
			try:
				clip_obj.name = target_name

				# Verify it worked
				actual_name = getattr(clip_obj, 'name', '')
				if target_name in actual_name or (len(target_name) <= 60 and target_name == actual_name):
					del self._meta_manager._pending_renames[i]
					processed_count += 1
				else:
					del self._meta_manager._pending_renames[i]
					processed_count += 1
			except RuntimeError:
				pass

		return processed_count

	def _setup_observer_listeners(self):
		"""Register for Live structural change notifications."""
		try:
			song = self.song()

			# Track structure changes (add/remove/reorder tracks)
			song.add_tracks_listener(self._on_tracks_changed)

			# Scene structure changes (affect clip slot indexing)
			song.add_scenes_listener(self._on_scenes_changed)
		except Exception as e:
			self._control_surface.log_message(f"[OBSERVER_REGISTRATION_ERROR] {e}")

	def _remove_observer_listeners(self):
		"""Unregister observers during disconnect to prevent memory leaks."""
		try:
			song = self.song()
			song.remove_tracks_listener(self._on_tracks_changed)
			song.remove_scenes_listener(self._on_scenes_changed)
		except:
			pass

	def _on_tracks_changed(self):
		"""Called when tracks are added/removed/reordered."""
		# Guard clause - skip if meta manager unavailable
		if not hasattr(self, '_meta_manager') or not self._meta_manager:
			return
		try:
			self._meta_manager.synchronize_all_clip_locations()
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._control_surface.log_message(f"[SYNC_ERROR] {e}\n{traceback.format_exc()}")

	def _on_scenes_changed(self):
		"""Called when scenes are added/removed/reordered."""
		if not hasattr(self, '_meta_manager') or not self._meta_manager:
			return
		try:
			self._meta_manager.synchronize_all_clip_locations()
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._control_surface.log_message(f"[SYNC_ERROR] {e}\n{traceback.format_exc()}")

	def set_clip(self, clip):
		"""Set current clip and load/restore its settings.
        Metadata priority:
           1. Embedded [SUX:{...}] tag on the clip
           2. Temporary JSON backup
           3. Factory defaults for genuinely new clips
        The clip tag is the source of truth whenever it can be parsed.
        JSON is only a recovery mechanism when the tag is missing/corrupt.
        """
		if DEBUG_LOGGING:
			self._set_clip_trace_seq = getattr(
				self, '_set_clip_trace_seq', 0
			) + 1
			seq = self._set_clip_trace_seq

			try:
				incoming_name = clip.name if clip is not None else 'None'
			except Exception:
				incoming_name = '<name unavailable>'

			current_clip = getattr(self, '_clip', None)

			try:
				current_name = (
					current_clip.name if current_clip is not None else 'None'
				)
			except Exception:
				current_name = '<name unavailable>'

			self._control_surface.log_message(
				'[CLIPTRACE EDITOR #%s] ENTER editor_id=%s '
				'incoming=%s incoming_id=%s current=%s current_id=%s'
				% (
					seq, id(self), incoming_name,
					id(clip) if clip is not None else None,
					current_name,
					id(current_clip) if current_clip is not None else None
				)
			)
		# NORMALIZE INPUT
		clip = self._normalize_clip(clip)
		# HANDLE EMPTY SLOT
		if clip is None:
			if self._meta_manager:
				try:
					track_idx = -1
					slot_idx = -1

					# Prefer the currently tracked ClipSlot.
					clip_slot = getattr(self, '_clip_slot', None)
					if clip_slot is not None:
						song = self.song()
						if song is not None:
							for t_idx, track in enumerate(song.tracks):
								for s_idx, slot in enumerate(track.clip_slots):
									if slot == clip_slot:
										track_idx = t_idx
										slot_idx = s_idx
										break
								if track_idx >= 0:
									break

					# Fallback: highlighted clip slot.
					if track_idx < 0:
						try:
							clip_slot = self.song().view.highlighted_clip_slot
							if clip_slot is not None:
								song = self.song()
								for t_idx, track in enumerate(song.tracks):
									for s_idx, slot in enumerate(track.clip_slots):
										if slot == clip_slot:
											track_idx = t_idx
											slot_idx = s_idx
											break
									if track_idx >= 0:
										break
						except Exception:
							pass
					if track_idx >= 0 and slot_idx >= 0:
						removed = (
							self._meta_manager
							.remove_clip_from_json_by_position(
								track_idx,
								slot_idx
							)
						)
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								f"[EMPTY_SLOT_JSON_CLEANUP] "
								f"T{track_idx}:S{slot_idx} "
								f"removed={removed}"
							)
					else:
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								"[EMPTY_SLOT_JSON_CLEANUP] "
								"Could not determine emptied slot position"
							)
				except Exception as e:
					if DEBUG_LOGGING:
						import traceback
						self._control_surface.log_message(
							f"[EMPTY_SLOT_JSON_CLEANUP_ERROR] "
							f"{e}\n{traceback.format_exc()}"
						)

			try:
				self._init_data()
			except Exception:
				pass
			self._clip = None
			self._focus_pitch = None
			self._force_update = True
			try:
				self.update()
			except Exception as e:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[EMPTY_SLOT_UPDATE_ERROR] {e}"
					)
			return

		# SAME CLIP - NOTHING TO DO
		if self._clip == clip:
			if DEBUG_LOGGING:
				# ADD MORE CONTEXT
				self._control_surface.log_message(
					'[CLIPTRACE EARLY_RETURN] same_clip=%s enabled=%s initializing=%s'
					% (self._clip == clip, self.is_enabled(), self._initializing)
				)
			return

		# PHASE 1  READ NEW CLIP METADATA
		# Tag is checked BEFORE JSON.
		clip_params = None
		settings_source = None
		clip_name = getattr(
			clip,
			'name',
			'<NO_NAME>'
		)
		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[CLIP_LOAD_START] "
				f"Examining clip name: '{clip_name[:100]}...'"
			)
		if self._meta_manager:
			try:
				clip_params = (self._meta_manager.extract_embedded_parameters(clip_name))
			except Exception as e:
				clip_params = None
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_EXTRACTION_ERROR] {e}"
					)
		if clip_params:
			settings_source = "EMBEDDED_TAG"
		else:
			if (self._meta_manager and hasattr(self._meta_manager,'recover_clip_settings_from_json')):
				try:
					json_result = (self._meta_manager.recover_clip_settings_from_json(clip))
				except Exception as e:
					json_result = None
					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[JSON_RECOVERY_ERROR] {e}"
						)
				if json_result:
					if (isinstance(json_result, dict) and json_result.get('requires_verification')):
						clip_params = json_result.get('settings')
						settings_source = "JSON_UNVERIFIED"
					else:
						clip_params = json_result
						settings_source = "JSON_MATCHED"
			if not clip_params:
				default_loop_start = 0.0
				try:
					default_loop_start = float(
						getattr(
							clip,
							'loop_start',
							0.0
						)
					)
				except Exception:
					default_loop_start = 0.0
				default_loop_end = 4.0
				try:
					default_loop_end = float(
						getattr(
							clip,
							'loop_end',
							4.0
						)
					)
				except Exception:
					default_loop_end = 4.0
				sux_defaults = {
					field_name: default_value
					for field_name, default_value, _validator in SUX_SCHEMA
				}
				clip_params = {
					'is_absolute': sux_defaults['is_absolute'],
					'is_drumrack': sux_defaults['is_drumrack'],
					'display_octave': sux_defaults['display_octave'],
					'resolution_index': sux_defaults['resolution_index'],
					'loop_block': sux_defaults['loop_block'],
					'loop_page_offset': sux_defaults['loop_page_offset'],
					'clip_loop_start': default_loop_start,
					'clip_loop_end': default_loop_end,
				}
				settings_source = "DEFAULTS"
		self._expected_drumrack = (bool(clip_params.get('is_drumrack', False)) if clip_params else False)
		if self._meta_manager and self._clip:
			try:
				settings_only = self._get_current_state_dict()
				track_idx, slot_idx = (self._meta_manager._get_current_track_slot_indices_safe(self._clip))
				save_payload = {
					**settings_only,
					'track_index': track_idx,
					'slot_index': slot_idx
				}
				self._meta_manager.save_clip_to_json(
					self._clip,
					save_payload
				)
			except Exception as e:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[PREV_STATE_SAVE_ERROR] {e}"
					)
		self._clip = clip
		self._focus_pitch = None
		self._init_data()


		if clip_params:
			try:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						'[CLIPTRACE EDITOR #%s] BEFORE load_clip_settings '
						'editor_id=%s clip_id=%s params=%s'
						% (seq, id(self), id(clip), clip_params)
					)

				self.load_clip_settings(clip, clip_params)

				if DEBUG_LOGGING:
					self._control_surface.log_message(
						'[CLIPTRACE EDITOR #%s] AFTER load_clip_settings '
						'editor_id=%s clip_id=%s'
						% (seq, id(self), id(clip))
					)

			except Exception:
				import traceback
				self._control_surface.log_message(
					'[LOAD_SETTINGS_ERROR] editor_id=%s clip_id=%s\n%s'
					% (id(self), id(clip), traceback.format_exc())
				)

		try:
			if self._meta_manager:
				final_state = self._get_current_state_dict()
				track_idx, slot_idx = (
					self._meta_manager
					._get_current_track_slot_indices_safe(
						clip
					)
				)
				final_state['track_index'] = track_idx
				final_state['slot_index'] = slot_idx
				if settings_source == "EMBEDDED_TAG":
					# No-op when the tag is current; upgrades tags written
					# with the old tag spacing or with stored markers.
					self.update_clip_name_with_params(
						clip,
						final_state
					)
					self._meta_manager.save_clip_to_json(
						clip,
						final_state
					)
				# JSON = RECOVERY SOURCE
				elif settings_source == "JSON_MATCHED":
					self.update_clip_name_with_params(
						clip,
						final_state
					)
					self._meta_manager.save_clip_to_json(
						clip,
						final_state
					)

				# GENUINELY NEW CLIP
				elif settings_source == "DEFAULTS":

					# The state was explicitly initialized from
					# FACTORY DEFAULTS above, so this no longer
					# inherits the previous clip's settings.

					self.update_clip_name_with_params(
						clip,
						final_state
					)
					self._meta_manager.save_clip_to_json(
						clip,
						final_state
					)
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._control_surface.log_message(
					f"[TAG_JSON_SYNC_ERROR] "
					f"{e}\n{traceback.format_exc()}"
				)
		self._initializing = False
		self._register_clip_slot_listener()
		self._force_update = True
		self.update()

		cname = (getattr(clip,'name','(unnamed)').split(' [')[0][:30] if clip and clip.name else "(unnamed)")
		if self._meta_manager:
			tidx, sidx = (self._meta_manager._get_current_track_slot_indices_safe(clip))
		else:
			tidx, sidx = (-1, -1)
		if DEBUG_LOGGING:
			selector_value = (
				self._step_sequencer._scale_selector.is_drumrack
				if hasattr(self._step_sequencer, '_scale_selector')
				else None
			)
			self._control_surface.log_message(
				f"[DRUMRACK_VERIFY] "
				f"clip_is_drumrack "
				f"selector={selector_value} "
				f"clip={getattr(self._clip, 'name', '<none>')[:40]}"
			)
		if hasattr(self._control_surface, 'schedule_message'):
			self._control_surface.schedule_message(2, self._settle_clip_mode)

	def _settle_clip_mode(self):
		if DEBUG_LOGGING:
			self._control_surface.log_message("[TRACE] _settle_clip_mode fired")
		if self._clip is None or not hasattr(self, '_expected_drumrack'):
			return
		expected = bool(self._expected_drumrack)
		selector_dr = None
		selector = getattr(self._step_sequencer, '_scale_selector', None)
		if selector is not None:
			selector_dr = bool(getattr(selector, '_is_drumrack', False))
		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[MODE_SETTLE] Selector is {selector_dr}, expected is {expected}"
			)
		if selector_dr is not None and selector_dr != expected:
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[MODE_SETTLE] Selector drifted {selector_dr} -> {expected}; re-applying"
				)
			selector._is_drumrack = expected
			if hasattr(selector, '_set_top_octave'):
				selector._set_top_octave(False)
		if bool(getattr(self, '_is_drumrack_layout', False)) != expected:
			self.on_drumrack_mode_changed(expected)
		self._step_sequencer._sync_editor_layout()
		# Drumrack clips don't use a scale: switch Live's Scale toggle off.
		# (Melodic clips keep whatever Scale setting they have in Live.)
		if expected and hasattr(self._step_sequencer, 'request_live_scale_mode'):
			self._step_sequencer.request_live_scale_mode(False)

	def manual_scan_all_clips(self):
		"""
		MANUAL TRIGGER: Call from long-press on StepSequencer2 button or Python console.
		Returns:
			tuple: (total_clips_scanned, clips_with_sux_tags_found)
		"""
		if not self._meta_manager:
			self._control_surface.show_message("No metadata manager available")
			return 0, 0

		# Run synchronously via scheduled message
		if hasattr(self._control_surface, 'schedule_message'):
			# Use lambda wrapper for callback
			result_container = {'total': 0, 'found': 0}

			def scan_wrapper():
				total, found = self._do_manual_scan()
				result_container['total'] = total
				result_container['found'] = found
			self._control_surface.schedule_message(1, scan_wrapper)

			# Wait briefly for scan to complete (non-blocking in Live thread)
			time.sleep(0.05)
			return result_container['total'], result_container['found']
		else:
			# Fallback without scheduling
			return self._do_manual_scan()

	def _do_manual_scan(self):
		if not self._meta_manager:
			return 0, 0
		total, found = self._meta_manager.scan_all_clips()
		msg = f"Scanned {total}, Found {found} tagged clips"
		self._control_surface.show_message(msg)
		self._control_surface.log_message(f"[MANUAL_SCAN] {msg}")
		return total, found

	def _get_simple_clip_key(self, clip):
		"""Fallback: Simple key using object identity when get_key() fails."""
		if not clip:
			return None
		clip_name = getattr(clip, 'name', 'Unknown')
		return f"FALLBACK_{id(clip)}::{clip_name}"

	def _get_current_state_dict(self):
		"""Returns current settings as dictionary WITHOUT modifying them."""
		# USE SCHEMA TO KNOW WHAT TO COLLECT
		state = {}
		for param_name, default_value, converter in SUX_SCHEMA:
			try:
				if param_name == 'is_absolute':
					if hasattr(self._step_sequencer, '_scale_selector') and self._step_sequencer._scale_selector:
						selector = self._step_sequencer._scale_selector
						current_val = getattr(selector, 'is_absolute', default_value)
						state[param_name] = current_val
					else:
						state[param_name] = default_value
				elif param_name == 'is_drumrack':
					if hasattr(self._step_sequencer, '_scale_selector') and self._step_sequencer._scale_selector:
						selector = self._step_sequencer._scale_selector
						current_val = getattr(selector, 'is_drumrack', default_value)
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								f"[IS_DRUMRACK_READ] from selector={current_val} (should match settings)"
							)
						state[param_name] = current_val
					else:
						state[param_name] = default_value
				elif param_name == 'display_octave':
					state[param_name] = int(getattr(self, '_display_octave', default_value))
				elif param_name == 'resolution_index':
					current_resolution = getattr(self, '_resolution', 16)
					from .SequencerConstants import RESOLUTION_MAP
					if isinstance(current_resolution, float):
						resolution_index = None
						for i, map_value in enumerate(RESOLUTION_MAP):
							if abs(map_value - current_resolution) < 0.0001:
								resolution_index = i
								break
						state[param_name] = resolution_index if resolution_index is not None else len(RESOLUTION_MAP) // 2
					else:
						state[param_name] = int(min(max(current_resolution, 0), len(RESOLUTION_MAP) - 1))
				elif param_name == 'loop_block':
					if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
						ls = self._step_sequencer._loop_selector
						state[param_name] = getattr(ls, '_block', default_value)
					else:
						state[param_name] = default_value
				elif param_name == 'loop_page_offset':
					if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
						ls = self._step_sequencer._loop_selector
						state[param_name] = getattr(ls, '_loop_page_offset', default_value)
					else:
						state[param_name] = default_value
				elif param_name == 'clip_loop_start':
					if self._clip:
						state[param_name] = round(getattr(self._clip, 'loop_start', default_value), 4)
					else:
						state[param_name] = default_value
				elif param_name == 'clip_loop_end':
					if self._clip:
						state[param_name] = round(getattr(self._clip, 'loop_end', default_value), 4)
					else:
						state[param_name] = default_value
			except Exception as e:
				if DEBUG_LOGGING:
					self._control_surface.log_message(f"[STATE_DICT_ERROR] {param_name}: {e}")
				state[param_name] = default_value
		state['timestamp'] = time.time()
		return state

	def load_clip_settings(self, clip, settings_dict):
		"""Loads settings from dictionary into active clip's UI state.
		CRITICAL FIXES:
		1. Resolution LED sync through proper setter chain
		2. Calculate page_offset from absolute block (don't save it)
		3. Force Loop Selector visual update
		4. Load loop_block onto ALL THREE COMPONENTS (Parent, Child, and Me)
		"""
		if DEBUG_LOGGING:
			self._control_surface.log_message('[load_clip_settings] called')
			self._control_surface.log_message(
				'[CLIPTRACE LOAD ENTER] '
				'class=%s editor_id=%s parent_id=%s '
				'clip_id=%s clip_name=%s params=%s'
				% (
					self.__class__.__name__,
					id(self),
					id(getattr(self, '_step_sequencer', None)),
					id(clip) if clip is not None else None,
					getattr(clip, 'name', '<no name>'),
					settings_dict
				)
			)
		if not settings_dict:
			if DEBUG_LOGGING:
				self._control_surface.log_message("[load_clip_settings] => No settings_dict")
			return
		self._loading_clip = True
		try:
			# === SCALE IS ABSOLUTE ===
			if 'is_absolute' in settings_dict and hasattr(self._step_sequencer, '_scale_selector'):
				selector = self._step_sequencer._scale_selector
				is_abs = bool(settings_dict.get('is_absolute', False))

				# Direct assignment to backing variable (most reliable)
				if hasattr(selector, '_is_absolute'):
					selector._is_absolute = is_abs

			# === SCALE IS DRUMRACK ===
			if (
					'is_drumrack' in settings_dict
					and hasattr(self._step_sequencer, '_scale_selector')
			):
				selector = self._step_sequencer._scale_selector
				is_dr = bool(settings_dict.get('is_drumrack', False))

				# Force the backing variable(s) directly
				selector._is_drumrack = is_dr
				if hasattr(selector, 'set_drumrack_from_clip'):
					selector.set_drumrack_from_clip(is_dr)
				else:
					selector._set_top_octave(False)

				# ⭐ Rebuild THIS editor's layout immediately, using the
				# value from the tag — guaranteed, regardless of what the
				# selector's property reports.
				self.on_drumrack_mode_changed(is_dr)
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[LOAD_IS_DRUMRACK] "
						f"clip={getattr(clip, 'name', '?')} "
						f"stored={is_dr} "
						f"selector_now={selector.is_drumrack}"
					)

			# === OCTAVE (melodic) / PITCH BLOCK (drumrack) ===
			if 'display_octave' in settings_dict:
				oct_val = int(settings_dict['display_octave'])
				oct_val = max(0, min(15, oct_val))
				old_oct = getattr(self, '_display_octave', 2)
				if oct_val != old_oct:
					self._display_octave = oct_val
					self._parse_notes()

			# === RESOLUTION: LOAD VALUE, LET SETTER HANDLE LED SYNC ===
			from .SequencerConstants import RESOLUTION_MAP
			new_resolution_beats = None

			# Try modern format first (resolution_index)
			if 'resolution_index' in settings_dict:
				res_idx = int(settings_dict['resolution_index'])

				# Validate bounds
				if res_idx >= len(RESOLUTION_MAP):
					res_idx = len(RESOLUTION_MAP) - 1
				if res_idx < 0:
					res_idx = 0

				# Save canonical value
				self._step_sequencer._resolution_index = res_idx
				# CONVERT INDEX → BEAT VALUE
				new_resolution_beats = float(RESOLUTION_MAP[res_idx])
			elif 'resolution' in settings_dict:
				res_val = int(settings_dict['resolution'])

				# ★★★ VALIDATION: Reject zero/negative resolutions ★★
				if res_val <= 0:
					new_resolution_beats = RESOLUTION_MAP[4]
				else:
					# If it's already a beat-like number (8, 16, 32, etc.), use directly
					new_resolution_beats = float(res_val)
			else:
				# No resolution field found - use safe default
				new_resolution_beats = RESOLUTION_MAP[4]  # Default 1/16th note

			# Apply the converted beat value ONLY if we got a valid conversion
			# Safety net: ensure resolution_never exceeds zero
			if new_resolution_beats is not None and new_resolution_beats > 0:
				old_res_beats = getattr(self, '_resolution', 0.25)
				self.apply_loaded_resolution(new_resolution_beats)
				self._parse_notes()
			else:
				# Fallback: Use minimum safe resolution
				min_resolution = min(RESOLUTION_MAP)
				old_res_beats = getattr(self, '_resolution', 0.25)
				self._resolution = min_resolution
				self._parse_notes()

			# === LOOP PARAMETERS: SYNC _loop_block ACROSS ALL THREE COMPONENTS ===
			if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
				ls = self._step_sequencer._loop_selector
				if 'loop_block' in settings_dict:
					loaded_loop_block = int(settings_dict['loop_block'])
					if hasattr(self._step_sequencer, '_loop_block'):
						old_parent_block = self._step_sequencer._loop_block
						self._step_sequencer._loop_block = loaded_loop_block
					if hasattr(ls, '_block'):
						old_ls_block = ls._block
						ls._block = loaded_loop_block
						ls._force = True
					if hasattr(self, '_loop_block'):
						old_me_block = self._loop_block
						self._loop_block = loaded_loop_block
				if hasattr(ls, '_block'):
					calculated_offset = ls._block // 8
				else:
					calculated_offset = 0
				if hasattr(self._step_sequencer, '_loop_page_offset'):
					old_offset = self._step_sequencer._loop_page_offset
					self._step_sequencer._loop_page_offset = calculated_offset
					if self._page != ls._block:
						# Force page change if offset changed
						if hasattr(self._step_sequencer, 'set_page'):
							absolute_block = ls._block
							self._step_sequencer.set_page(absolute_block)
				if hasattr(ls, '_loop_page_offset'):
					# IMPORTANT: Reset last_known_offset so Update detects the change
					ls._last_known_offset = calculated_offset - 1  # Force detection on next update
					ls._loop_page_offset = calculated_offset

				# === LOAD LOOPS DIRECTLY FROM TAG VALUES ===
				if 'clip_loop_start' in settings_dict and 'clip_loop_end' in settings_dict:
					if self._clip:
						start = float(settings_dict['clip_loop_start'])
						end = float(settings_dict['clip_loop_end'])
						if end > start:
							try:
								# The loop stays within the clip's markers;
								# the markers themselves are never changed.
								clamped = clamp_to_markers(self._clip, start, end)
								if clamped is not None:
									apply_clip_loop(self._clip, *clamped)
								if hasattr(ls, '_get_clip_loop'):
									ls._get_clip_loop()
									ls.update()
							except RuntimeError:
								pass

				# === CRITICAL: FORCE LOOP SELECTOR VISUAL UPDATE ===
				# After all loop parameters are set, force full redraw
				if hasattr(ls, 'update'):
					ls.update()
				if hasattr(self._step_sequencer, '_update_cycle_button'):
					try:
						self._step_sequencer._update_cycle_button()
					except Exception as e:
						if DEBUG_LOGGING:
							self._control_surface.log_message(f"[CYCLE_BUTTON_ERROR] {e}")

			# === CRITICAL: FORCE RESOLUTION BUTTON LED UPDATE ===
			# Now that we've loaded the resolution, trigger the proper setter chain
			# This will update both LED display AND loop selector boundaries
			if self._step_sequencer and hasattr(self._step_sequencer, '_note_editor'):
				# The resolution was already set above, but we need to refresh the UI
				# Call through proper setter to ensure LED sync
				try:
					self._step_sequencer.refresh_after_clip_load()
				except Exception as e:
					if DEBUG_LOGGING:
						self._control_surface.log_message(f"[RESOLUTION_LED_SYNC_ERROR] {e}")
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[SETTINGS_APPLIED] Oct={settings_dict.get('display_octave')} "
					f"IsDrumRack={settings_dict.get('is_drumrack')} "
					f"ResIdxOrBeats={settings_dict.get('resolution_index', settings_dict.get('resolution'))} "
					f"IsAbsolue={settings_dict.get('is_absolute')} "
					f"LoopBlock={settings_dict.get('loop_block', 'MISSING')}"
				)
		except Exception as e:
			import traceback
			self._control_surface.log_message(f"[SET_LOADING ERROR] {e}\n{traceback.format_exc()}")

		if hasattr(self._step_sequencer, '_sync_editor_layout'):
			self._step_sequencer._sync_editor_layout()
		self._loading_clip = False
		# FINAL CLIP LAYOUT SYNC
		# At this point ALL clip settings have been restored.
		# Now synchronize the note editor's physical layout with
		# the newly loaded clip's stored drumrack state.
		try:
			selector = getattr(self._step_sequencer, '_scale_selector', None)
			if selector is not None:
				self._step_sequencer._sync_editor_layout()
			else:
				# Selector unavailable — trust the tag value
				self.on_drumrack_mode_changed(bool(self._is_drumrack_layout))
		except Exception as e:
			self._control_surface.log_message(f"[TAIL_LAYOUT_SYNC_ERROR] {e}")
		if hasattr(self._step_sequencer, '_sync_editor_layout'):
			if DEBUG_LOGGING:
				selector_dr = bool(
					self._step_sequencer._scale_selector.is_drumrack
				)
				editor_dr = bool(
					getattr(
						self._step_sequencer._note_editor,
						'_is_drumrack_layout',
						False
					)
				)
				self._control_surface.log_message(
					f"[FINAL_LAYOUT_SYNC] "
					f"clip={getattr(clip, 'name', '?')} "
					f"selector={selector_dr} "
					f"editor={editor_dr}"
					f" key index={self._key_indexes}"
				)

		# Force refresh
		self._force_update = True
		self.update()
		self._verify_resolution_consistency()

		# After all operations in load_clip_settings, verify state
		if DEBUG_LOGGING and hasattr(self._step_sequencer, '_scale_selector'):
			selector = self._step_sequencer._scale_selector
			if hasattr(selector,
			           'is_drumrack') and selector.is_drumrack != is_dr:
				self._control_surface.log_message(
					f"[STATE_MISMATCH] Expected {is_dr}, got {selector.is_drumrack} - potential override detected"
				)
		if DEBUG_LOGGING:
			self._control_surface.log_message(
				'[CLIPTRACE SETTINGS] editor_id=%s clip_id=%s '
				'is_drumrack=%s expected_drumrack=%s '
				'octave=%s resolution=%s loop_block=%s '
				'loop_start=%s loop_end=%s'
				% (
					id(self),
					id(clip) if clip is not None else None,
					getattr(self._step_sequencer._scale_selector, 'is_drumrack', 'is_drumrack'),
					getattr(self, '_expected_drumrack', '_expected_drumrack'),
					getattr(self, '_octave', 'display_octave'),
					getattr(self, '_resolution', 'resolution_index'),
					getattr(self, '_loop_block', 'loop_block'),
					getattr(self, '_loop_start', 'clip_loop_start'),
					getattr(self, '_loop_end', 'clip_loop_end')
				)
			)

		if DEBUG_LOGGING:
			self._control_surface.log_message(
				'[CLIPTRACE LOAD EXIT] '
				'editor_id=%s parent_id=%s clip_id=%s '
				'selector_drumrack=%s editor_layout=%s'
				% (
					id(self),
					id(getattr(self, '_step_sequencer', None)),
					id(clip) if clip is not None else None,
					getattr(
						getattr(self, '_scale_component', None),
						'_is_drumrack',
						'<unknown>'
					),
					getattr(self, '_is_drumrack_layout', '<unknown>')
				)
			)

	def _verify_resolution_consistency(self):
		"""Debug helper to ensure the note editor and StepSequencer agree."""
		if not DEBUG_LOGGING:
			return
		expected = RESOLUTION_MAP[self._step_sequencer._resolution_index]
		if abs(expected - self._resolution) > 1e-6:
			self._control_surface.log_message(
				f"[RESOLUTION_MISMATCH] "
				f"index={self._step_sequencer._resolution_index} "
				f"expected={expected} "
				f"editor={self._resolution}"
			)

	def apply_loaded_resolution(self, beats):
		"""
		Called only while loading a clip.
		Updates the editor without triggering JSON sync.
		"""
		self._resolution = beats
		if self._clip:
			self._parse_notes()

	# --- HELPER METHODS FOR METADATA MANAGER ---
	def _load_metadata_cache(self):
		"""Loads the JSON file into memory."""
		if not self._metadata_file_path or not os.path.exists(self._metadata_file_path):
			return
		try:
			with open(self._metadata_file_path, 'r') as f:
				self._metadata_cache = json.load(f)
		except Exception as e:
			self._control_surface.log_message(f"[META] Failed to load cache: {e}")
			self._metadata_cache = {}

	def _save_metadata_cache(self):
		"""Writes the cache back to the JSON file."""
		if not self._metadata_file_path:
			return
		try:
			with open(self._metadata_file_path, 'w') as f:
				json.dump(self._metadata_cache, f, indent=2)
		except Exception as e:
			self._control_surface.log_message(f"[META] Failed to save cache: {e}")

	def _normalize_clip(self, clip):
		"""Utility: Convert ClipSlot to Clip if necessary."""
		if not clip:
			return None
		if hasattr(clip, 'has_clip'):
			if clip.has_clip:
				return clip.clip
			return None
		return clip

	def _get_clip_id(self, clip):
		"""Creates a unique string ID - handles None safely."""
		clip = self._normalize_clip(clip)
		if not clip:
			return None
		try:
			# Same traversal logic as get_key()
			parent = clip.canonical_parent
			while parent is not None:
				parent_type = type(parent).__name__
				if parent_type == 'Track':
					break
				elif parent_type in ['Song', 'Scene']:
					parent = None
					break
				else:
					parent = getattr(parent, 'canonical_parent', None)
			track = parent
			track_name = track.name if track else "UnknownTrack"
			clip_name = clip.name if clip.name else "UnnamedClip"
			start_time = getattr(clip, 'start_time', 0)
			return f"{track_name}::{clip_name}::{int(start_time)}"
		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[GetID Error] {e}")
			return None

	def get_clip_data(self, clip):
		"""Retrieves saved data for a clip."""
		if not self._meta_manager or not clip:
			return None
		return self._meta_manager.get(clip)

	def save_clip_data(self, clip, data_dict):
		"""Saves data dict via the manager."""
		if not self._meta_manager or not clip:
			return
		primary_key = self._meta_manager._identify_clip_by_position(clip)
		if primary_key is None:
			primary_key = self._get_simple_clip_key(clip)
		if primary_key:
			self._meta_manager.cache[primary_key] = data_dict
			self._meta_manager._save_cache()

	def cleanup_missing_clips(self):
		"""Scan cache for clips that no longer exist."""
		song = self.song()
		existing_ids = set()

		# Collect all valid IDs
		for track in song.tracks:
			for slot in track.clip_slots:
				if slot.has_clip:
					_, clip_id = self._meta_manager.strip_clip_id_tag(getattr(slot.clip, 'name', ''))
					if clip_id:
						existing_ids.add(clip_id)

		# Remove orphaned entries
		orphans = []
		for cid in list(self._meta_manager.cache.get("clips", {}).keys()):
			if cid not in existing_ids:
				orphans.append(cid)
				del self._meta_manager.cache["clips"][cid]

	def set_enabled(self, enabled):
		ControlSurfaceComponent.set_enabled(self, enabled)

		# Avoid redundant updates if already in this state
		if hasattr(self, '_enabled_state'):
			if self._enabled_state == enabled:
				return
			self._enabled_state = enabled
		else:
			self._enabled_state = enabled
		if enabled and hasattr(self, '_mode_copy_paste_button') and self._mode_copy_paste_button:
			self._update_copy_paste_button_state()
			self._force_update = True
			self.update()

	def _init_data(self):
		pages = 4096
		self._editing_step = None
		self._editing_step_pitches = []
		self._pending_velocity_editor = False
		self._velocity_wait_animation = False
		self._velocity_wait_start_times = [0] * 8
		self._pending_length_editor = False
		self._length_wait_animation = False
		self._length_wait_start_times = [0] * 8
		self._is_velocity_editor_vertical = False
		self._last_velocity_press_time = 0
		self._last_velocity_press_pos = None
		# Velocity/length slider pad currently held (fine adjustment gesture), or None
		self._slider_hold = None
		# Notes view pads currently held (press order), see _press_note_pad
		self._held_note_pads = []
		# Notes moved through the navigation view (see _land_moved_notes):
		# captured when the navigation button is pressed, then being moved
		self._nav_move_capture = None
		self._nav_move = None
		# Navigation button in the notes view: tap waiting for a 2nd one
		# (release time), and whether the press in progress is that 2nd tap
		self._nav_button_pending_tap = None
		self._nav_button_double_tap = False
		# View positions (see _current_position): when the navigation view
		# was entered, and the one a double tap goes back to
		self._nav_entry_position = None
		self._previous_position = None
		self._double_click_window = 0.25
		self._pending_revert_data = None
		self._revert_timer_id = None
		self._last_length_press_time = 0
		self._last_length_press_pos = None
		self._is_length_editor_vertical = False
		rows = 8 if getattr(self, '_is_drumrack_layout', False) else 7
		self._notes_pitches = [0] * (rows * pages)
		self._notes_velocities = [4] * pages
		self._notes_octaves = [2] * pages
		self._notes_lengths = [3] * pages
		# IMPORTANT:
		# _display_octave is persistent clip state.
		# Initialize it only if this component has never had one.
		if not hasattr(self, '_display_octave'):
			self._display_octave = 2

	def set_mode(self, mode):
		old_mode = self._mode
		self._mode = mode
		self._force_update = True
		# Any mode change cancels a pending navigation-view button gesture
		self._nav_held = []
		self._nav_combo_done = False
		self._nav_pending_tap = None
		self._slider_hold = None
		self._held_note_pads = []
		if mode != STEPSEQ_MODE_NAV_OVERVIEW:
			self._nav_move = None
		if hasattr(self, '_step_sequencer') and self._step_sequencer:
			self._step_sequencer.update()

		# --- ACCESS LOOP SELECTOR SAFELY ---
		loop_selector = None
		if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
			loop_selector = self._step_sequencer._loop_selector

		# --- 1: DISABLE LOOP SELECTOR WHEN ENTERING OCTAVE OVERVIEW ---
		if old_mode != STEPSEQ_MODE_NAV_OVERVIEW and mode == STEPSEQ_MODE_NAV_OVERVIEW:
			if loop_selector:
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
		elif old_mode == STEPSEQ_MODE_NAV_OVERVIEW and mode != STEPSEQ_MODE_NAV_OVERVIEW:
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
			STEPSEQ_MODE_NAV_OVERVIEW
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
					  STEPSEQ_MODE_COPY_PASTE, STEPSEQ_MODE_NAV_OVERVIEW]:
			if loop_selector and self.uses_bottom_row():
				if loop_selector.is_enabled():
					loop_selector.set_enabled(False)
					loop_selector._cache = [-1] * len(loop_selector._buttons)
					loop_selector._force = True
			if mode == STEPSEQ_MODE_NAV_OVERVIEW:
				# Capture the octave we are currently displaying before switching modes
				self._last_displayed_octave = self._display_octave

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
		if mode == STEPSEQ_MODE_NOTES:
			self._update_copy_paste_button_state()
		elif mode != STEPSEQ_MODE_NOTES:
			pass
		self.update()

	def on_drumrack_mode_changed(self, is_drumrack):
		old_octave = self._display_octave
		was_drumrack = bool(getattr(self, '_is_drumrack_layout', False))
		self._is_drumrack_layout = bool(is_drumrack)
		# _display_octave means "pitch block" in drumrack layout and
		# "octave" in melodic layout: keep the value but make sure it is
		# valid for the layout we are switching to.
		if was_drumrack != bool(is_drumrack):
			old_octave = max(0, min(self._nav_band_count() - 1, old_octave))
			# Focus mode is melodic only
			self._focus_pitch = None
		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[NOTE_EDITOR_SYNC] Setting _is_drumrack_layout to {is_drumrack}"
			)
		# ⭐ NEW FLAG TO DEFER HARDWARE UPDATE ⭐
		if not getattr(self, '_layout_sync_pending', False):
			self._init_data()
			self._display_octave = old_octave
			self._parse_notes()
			self._force_update = True
			self.update()
		else:
			# Just update internal state, wait for sync to trigger update
			self._init_data()
			self._display_octave = old_octave
			self._parse_notes()

	@property
	def _is_drumrack_layout(self):
		return self._dr_layout_state

	@_is_drumrack_layout.setter
	def _is_drumrack_layout(self, value):
		value = bool(value)
		old = getattr(self, '_dr_layout_state', None)
		if old is None or old != value:
			try:
				import traceback
				self._control_surface.log_message(
					"[DR_STATE] Editor._is_drumrack_layout %s -> %s\n%s" % (
						old, value, "".join(traceback.format_stack(limit=7))
					)
				)
			except Exception:
				pass
		self._dr_layout_state = value

	@property
	def _note_rows(self):
		ss = getattr(self, '_step_sequencer', None)
		if ss is not None and getattr(ss, '_scale_selector', None) is not None:
			return 8 if ss._scale_selector.is_drumrack else 7
		return 7

	# ------------------------------------------------------------------
	# PITCH MAPPING HELPERS (melodic octaves vs. drumrack pitch blocks)
	# ------------------------------------------------------------------
	@property
	def _is_drum_view(self):
		"""True when the grid is laid out as chromatic 8-note pitch blocks."""
		return bool(getattr(self, '_is_drumrack_layout', False))

	@property
	def _is_focus_view(self):
		"""True in focus mode: the melodic rows are the semitones around a note."""
		return getattr(self, '_focus_pitch', None) is not None and not self._is_drum_view

	def _row_pitch(self, row_idx):
		"""
		MIDI pitch shown on a note row. row_idx 0 = bottom row (LP row 7).
		Drumrack: pitch block b -> row 7 = b*8, row 6 = b*8+1, ... row 0 = b*8+7
		          (block 0: row 7 = MIDI 0 (C-2), row 5 = MIDI 2, ...)
		Focus   : the focus pitch on LP row FOCUS_ROW, one semitone per row.
		Melodic : scale degree from key_indexes, shifted by display octave.
		"""
		if self._is_drum_view:
			return self._display_octave * DRUM_BLOCK_SIZE + row_idx
		if self._is_focus_view:
			return self._focus_pitch + row_idx - (self._note_rows - 1 - FOCUS_ROW)
		return self._key_indexes[row_idx] + 12 * (self._display_octave - 2)

	def _nav_band_count(self):
		"""Number of vertical bands in the navigation view (16 blocks / 11 octaves)."""
		return DRUM_BLOCK_COUNT if self._is_drum_view else MELODIC_OCTAVE_COUNT

	def _nav_max_start(self):
		"""Highest first-visible band so that 8 bands fit in the view."""
		return max(0, self._nav_band_count() - NAV_VISIBLE_BANDS)

	def _nav_band_of_pitch(self, pitch):
		"""Navigation band (pitch block or MIDI octave) a MIDI pitch belongs to."""
		if self._is_drum_view:
			return int(pitch) // DRUM_BLOCK_SIZE
		return int(pitch) // 12

	def _displayed_nav_band(self, display_value=None, focus_pitch=None):
		"""Band shown in the note grid (default: now). Focus mode: the focus pitch's octave."""
		if display_value is None:
			display_value = self._display_octave
			focus_pitch = self._focus_pitch
		if self._is_drum_view:
			return display_value
		if focus_pitch is not None:
			return int(focus_pitch) // 12
		# melodic: display octave 2 shows MIDI octave 3 (C1 = 36)
		return display_value + 1

	def _nav_band_label(self, band):
		"""Human-readable label for a navigation band."""
		if self._is_drum_view:
			low = band * DRUM_BLOCK_SIZE
			high = min(127, low + DRUM_BLOCK_SIZE - 1)
			return "Pitch block %d (MIDI %d-%d)" % (band, low, high)
		return "Octave %d" % (band - 2)

	def _view_band_unit(self):
		"""Semitone distance between two adjacent view bands."""
		return DRUM_BLOCK_SIZE if self._is_drum_view else 12

	def _current_view_band(self):
		"""Band (block / MIDI octave) currently visible in the note grid."""
		if self._is_drum_view:
			return self._display_octave
		return int((self._key_indexes[0] + 12 * (self._display_octave - 2)) / 12)

	def _is_pitch_in_current_view(self, pitch):
		"""
		Copy / paste / erase: a pitch of the current view, i.e. of the
		displayed octave / pitch block, or in focus mode of the rows shown.
		"""
		if self._is_focus_view:
			return self._visible_row_for_pitch(pitch) >= 0
		return self._nav_band_of_pitch(pitch) == self._current_view_band()

	def _push_to_hardware(self):
		"""Force ALL buffered LED values to hardware immediately, ignoring cache."""
		if self._matrix != None:
			for x in range(8):
				for y in range(8):
					# Always compare back_buffer vs grid_buffer
					if self._grid_back_buffer[x][y] != self._grid_buffer[x][y]:
						self._grid_buffer[x][y] = self._grid_back_buffer[x][y]
						button = self._matrix.get_button(x, y)
						if button:
							try:
								button.set_light(self._grid_buffer[x][y])
							except RuntimeError:
								pass # Ignore errors if matrix is gone
			# Force the main loop to skip its own update check next time
			self._force_update = False

	def set_note_cache(self, note_cache):
		if self._note_cache != note_cache:
			self._note_cache = note_cache
			self._parse_notes()

	def set_playhead(self, playhead):
		self._playhead = playhead
		if self.is_enabled():
			self._update_matrix()

	def set_multinote(self, x=0, y=0):
		pass

	def set_resolution(self, resolution):
		"""Set resolution with validation BEFORE storage."""

		# === VALIDATION PHASE (before any storage) ===
		if resolution <= 0:
			resolution = 16
		old_resolution = self._resolution if hasattr(self, '_resolution') else 0.25
		self._resolution = resolution
		# Update internal buffers if clip exists
		if self._clip is not None:
			self._parse_notes()
			self._update_matrix()

		# ⭐⭐⭐ CRITICAL: SYNC loop_block with parent BEFORE saving ⭐⭐⭐
		if hasattr(self._step_sequencer, '_loop_block'):
			if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
				ls = self._step_sequencer._loop_selector
				# Sync parent with child
				self._step_sequencer._loop_block = getattr(ls, '_block', 0)
		if not getattr(self, "_loading_clip", False):
			self.sync_clip_with_json()

	def set_diatonic(self, diatonic):
		self._diatonic = diatonic

	def set_key_indexes(self, key_indexes):
		if self._key_indexes != key_indexes:
			self._key_indexes = key_indexes

	def set_key_index_is_in_scale(self, key_index_is_in_scale):
		self._key_index_is_in_scale = key_index_is_in_scale

	def set_key_index_is_root_note(self, key_index_is_root_note):
		self._key_index_is_root_note = key_index_is_root_note

	def set_page(self, page):
		self._page = page

	def _get_notes_at_step(self, idx):
		start_time = idx * self.resolution_beats
		end_time = start_time + self.resolution_beats
		return [note for note in self._note_cache if start_time <= note[1] < end_time]

	def _get_note_for_pitch_at_step(self, idx, pitch):
		start_time = idx * self.resolution_beats
		end_time = start_time + self.resolution_beats
		for note in self._note_cache:
			if (
					start_time <= note[1] < end_time
					and note[0] == pitch
			):
				return note
		return None

	def _parse_notes(self):
		# Safety check: Ensure arrays are initialized
		if not hasattr(self, '_notes_velocities') or not self._notes_velocities:
			return

		pages = 1024  # Match your original allocation
		step_count = min(pages, len(self._notes_velocities))  # Use actual available slots

		# clear notes
		for i in range(len(self._notes_pitches)):
			self._notes_pitches[i] = 0

		# Initialize safe-sized buffer
		first_note = [True] * step_count
		try:
			for note in self._note_cache:
				note_key = note[0]
				note_position = note[1]
				note_length = note[2]
				note_velocity = note[3]
				note_muted = note[4]
				if note_muted:
					continue

				# CRITICAL: Calculate step index with bounds protection
				i = int(note_position / self.resolution_beats)

				# SAFETY CHECK: Skip notes that fall outside our array range
				if i >= step_count or i < 0:
					continue

				# Process velocity/length
				if first_note[i]:
					first_note[i] = False
					for x in range(7):
						if note_velocity >= self._velocity_map[x]:
							self._notes_velocities[i] = x
					for x in range(7):
						if note_length * 4 >= self._length_map[x] * self.resolution_beats:
							self._notes_lengths[i] = x

				# Process pitch display
				rows = self._note_rows
				for j in range(min(rows, len(self._key_indexes))):
					display_pitch = self._row_pitch(j)
					if note_key == display_pitch:
						# Another safety check for pitches array
						pitch_idx = i * rows + j
						if pitch_idx < len(self._notes_pitches):
							self._notes_pitches[pitch_idx] = 1
		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[PARSE NOTES ERROR] {e}")

	def _toggle_note_at_grid_position(self, idx, y):
		self._toggle_note(idx, self._row_pitch(self._note_rows - 1 - y))

	def _toggle_note(self, idx, pitch):
		grid_time = idx * self.resolution_beats
		if pitch < 0 or pitch > 127:
			return
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
				self.resolution_beats,
				self._note_velocity,
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
			rows = self._note_rows
			for x in range(len(self._notes_velocities)):
				for note_index in range(rows):
					if self._notes_pitches[x * rows + note_index] == 1:
						note_time = x * self.resolution_beats
						velocity = self._velocity_map[self._notes_velocities[x]]
						length = self._length_map[self._notes_lengths[x]] * self.resolution_beats / 4.0
						if self._is_drum_view or self._is_focus_view:
							pitch = self._row_pitch(note_index)
						else:
							pitch = self._key_indexes[note_index] + 12 * (self._notes_octaves[x] - 2)
						if(pitch >= 0 and pitch < 128 and velocity >= 0 and velocity < 128 and length >= 0):
							note_cache.append([pitch, note_time, length, velocity, False])
			self._clip.select_all_notes()
			self._clip.replace_selected_notes(tuple(note_cache))

	def _sch_update(self, data):
			clip = data[0]
			note_cache = data[1]
			clip.select_all_notes()
			if(note_cache == None):
				clip.replace_selected_notes(tuple())
			else:
				clip.replace_selected_notes(note_cache)

	def update(self, force=False):
		if force:
			self._force_update = True
		if self.is_enabled():
			self._update_mode_notes_octaves_button()
			self._update_mode_notes_lengths_button()
			self._update_mode_notes_velocities_button()
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
		self._force_update = True

	def _update_matrix(self):
		if hasattr(self, '_step_sequencer') and self._step_sequencer:
			has_scale = hasattr(self._step_sequencer, '_scale_selector') and self._step_sequencer._scale_selector
			if has_scale:
				try:
					root_key = self._step_sequencer._scale_selector._key
				except AttributeError as e:
					self._control_surface.log_message(f"[UPDATE_MATRIX] ERROR ACCESSING ROOT_KEY: {e}")

		# Check if we are in an animation state to prevent buffer clearing
		in_animation = self._velocity_wait_animation or self._length_wait_animation

		# Define effective_page at the start - needed for both animation and normal rendering
		effective_page = self._get_effective_page()

		# WAITING ANIMATION: Velocity
		# Both animations cover all 8 rows: the note editor owns row 7 while
		# waiting (uses_bottom_row), the loop selector is disabled meanwhile.
		rows = self._note_rows
		if self._velocity_wait_animation:
			now = time.time()
			for x in range(8):
				delay = self._velocity_wait_start_times[x]

				# Calculate elapsed time since the specific column's start
				elapsed_col = now - delay

				# If this is the very first run or timer is in future, force "Waiting" state
				if elapsed_col <= 0.0:
					# Clear column only
					for y in range(8):
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"
					continue

				# --- CONFIGURATION ---
				SPAWN_COEFFICIENT = 2.5  # Tune this: Lower = faster spawns
				FALL_SPEED = 12.0
				time_to_fall = 8.0 / FALL_SPEED
				base_wait = (x * 0.37) % 1.0
				random_wait = base_wait * SPAWN_COEFFICIENT
				cycle_duration = time_to_fall + random_wait

				# Handle potential float precision issues
				time_in_cycle = elapsed_col % cycle_duration

				head_pos = -1  # Default to nothing visible

				# Only draw if we are in the "falling" phase of the cycle
				if time_in_cycle < time_to_fall:
					pos_float = (time_in_cycle / time_to_fall) * 8.0
					head_pos = int(pos_float)
					if head_pos > 7: head_pos = 7

				# Clear Column (Always Black first)
				for y in range(8):
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
					for y in range(8):
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"
					continue

				# Configuration
				SPAWN_COEFFICIENT = 2.0
				FALL_SPEED = 12.0
				time_to_fall = 8.0 / FALL_SPEED
				base_wait = ((x + 1) * 0.43) % 1.0
				random_wait = base_wait * SPAWN_COEFFICIENT
				cycle_duration = time_to_fall + random_wait
				time_in_cycle = elapsed_col % cycle_duration
				head_pos = -1
				if time_in_cycle < time_to_fall:
					pos_float = (time_in_cycle / time_to_fall) * 8.0
					head_pos = int(pos_float)
					if head_pos > 7: head_pos = 7
				for y in range(8):
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
				for x in range(8):
					for y in range(rows):
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"
				# update back buffer
				if self._clip != None:
					# Notes view: one pass per step column. The other views draw
					# the whole grid themselves, so they run once per update.
					if self._mode == STEPSEQ_MODE_NOTES:
						for x in range(8):
							idx = self._get_step_index(x)
							# --- SCALE CONFIGURATION ---
							scale_root_key = 0
							scale_notes = [0, 2, 4, 5, 7, 9, 11]
							if hasattr(self, "_step_sequencer") and self._step_sequencer and \
									hasattr(self._step_sequencer, "_scale_selector") and \
									self._step_sequencer._scale_selector:
								selector = self._step_sequencer._scale_selector
								scale_root_key = selector._key
								scale_notes = [n % 12 for n in selector.notes]
							start_time = idx * self.resolution_beats
							end_time = start_time + self.resolution_beats
							step_notes_list = self._get_notes_at_step(idx)
							for y in range(rows):
								row_idx = (rows-1) - y
								current_pitch = self._row_pitch(row_idx)
								is_midi_valid = (0 <= current_pitch <= 127)
								target_color = "StepSequencer2.Pitch.Off"
								# checkboard for invalid pitches-
								if not is_midi_valid:
									if (x + y) % 2 == 0:
										target_color = "StepSequencer2.Pitch.OutOfRange"
									else:
										target_color = "StepSequencer2.Pitch.Off"
								else:
									notes_mapped_to_this_row = []
									for n in step_notes_list:
										note_midi = n[0]
										note_time = n[1]

										# 1. Time Check
										if not (start_time <= note_time < end_time):
											continue

										# 2. Row Mapping Check (melodic: closest row,
										# only if the note is in that row's octave)
										if self._visible_row_for_pitch(note_midi) != row_idx:
											continue
										notes_mapped_to_this_row.append(n)
									has_note_here = len(notes_mapped_to_this_row) > 0
									if self._is_drum_view:
										# Drumrack: colour by drum lane (MIDI pitch % 16)
										lane = current_pitch % 16
										target_color = (DRUM_COLORS_NOTE[lane] if has_note_here
														else DRUM_COLORS_NO_NOTE[lane])
									elif self._is_focus_view:
										target_color = self._focus_pad_color(
											current_pitch, notes_mapped_to_this_row, scale_notes)
									elif not has_note_here:
										# No note here: Draw Scale Colors
										interval = (current_pitch - scale_root_key) % 12
										is_root = (interval == 0)
										if is_root:
											target_color = "StepSequencer2.Pitch.RootNote"
										else:
											target_color = "StepSequencer2.Pitch.Off"
									# NO CONTINUE HERE -> ensures buffer update

									else:
										# --- NOTE PRESENT LOGIC ---
										has_on_step = False
										has_off_step = False
										has_in_scale = False
										has_out_scale = False
										for n in notes_mapped_to_this_row:
											note_time = n[1]
											note_deg = n[0] % 12
											if self._is_note_on_grid(note_time, self.resolution_beats):
												has_on_step = True
											else:
												has_off_step = True
											if note_deg in scale_notes:
												has_in_scale = True
											else:
												has_out_scale = True

										# --- COLOR DETERMINATION ---
										target_color = "StepSequencer2.Pitch.On"
										if has_out_scale and has_off_step and has_in_scale:
											target_color = "StepSequencer2.Pitch.OnMixedStepScale"
										elif not has_in_scale and not has_on_step:
											target_color = "StepSequencer2.Pitch.OnOutScaleOffStep"
										elif has_out_scale and not has_in_scale and has_off_step:
											target_color = "StepSequencer2.Pitch.OnOutScaleOffStep2"
										elif has_in_scale and has_out_scale and not has_off_step:
											target_color = "StepSequencer2.Pitch.OnMixedScale"
										elif has_on_step and has_off_step and not has_out_scale:
											target_color = "StepSequencer2.Pitch.OnMixedStep"
										elif has_out_scale and not has_in_scale and not has_off_step:
											target_color = "StepSequencer2.Pitch.OnOutScale"
										elif has_off_step and not has_on_step and not has_out_scale:
											target_color = "StepSequencer2.Pitch.OnOffStep"
										else:
											target_color = "StepSequencer2.Pitch.On"

								# Update buffer for ALL cases
								self._grid_back_buffer[x][y] = target_color
					elif self._mode == STEPSEQ_MODE_NAV_OVERVIEW:
						# Vertical bands: MIDI octaves (melodic, 11 bands) or
						# 8-note pitch blocks (drumrack, 16 bands).
						start_band = self._overview_start_octave
						max_visible_band = start_band + 7
						min_visible_band = start_band
						band_count = self._nav_band_count()
						displayed_band = self._displayed_nav_band(
							self._last_displayed_octave, self._focus_pitch)

						# --- LATERAL ZOOM: columns are BLOCKS (8 steps each), not steps ---
						page_offset = getattr(self._step_sequencer, '_loop_page_offset', 0)

						# NOTE: the playhead is intentionally NOT painted in this view.

						# Currently selected block (absolute, from LoopSelector source of truth)
						ls = getattr(self._step_sequencer, '_loop_selector', None)
						selected_block = getattr(ls, '_block', None) if ls else None

						# Current clip loop, read live from the clip (also reflects
						# loop edits made in Live)
						try:
							loop_start = float(self._clip.loop_start)
							loop_end = float(self._clip.loop_end)
						except Exception:
							loop_start, loop_end = 0.0, 0.0
						block_len = 8 * self.resolution_beats
						if self._nav_move is not None:
							# Moving notes: the cross is centred on where they come
							# from; tapping its centre cancels the move.
							selected_block = int(self._nav_move['anchor_time'] // block_len)
							displayed_band = self._displayed_nav_band(
								self._nav_move['display_octave'], self._nav_move['focus_pitch'])
						clip_end = self._clip_total_end()
						for x in range(8):
							block_index = x + page_offset * 8
							# Blocks beyond the clip's total length are not painted
							if block_index * block_len >= clip_end:
								for y in range(8):
									self._grid_back_buffer[x][y] = "DefaultButton.Disabled"
								continue
							step_notes = self._get_notes_in_block(block_index)
							note_bands = set(self._nav_band_of_pitch(n[0]) for n in step_notes)
							highest_note_band = max(note_bands) if note_bands else -1
							lowest_note_band = min(note_bands) if note_bands else 128
							is_selected_col = (selected_block == block_index)
							# Same rule as the loop selector row: a block is in the
							# loop when its first step lies inside [loop_start, loop_end)
							block_start = block_index * block_len
							is_loop_col = (loop_start <= block_start < loop_end)
							for y in range(8):
								target_band = start_band + (7 - y)
								has_note_here = target_band in note_bands
								target_color = "StepSequencer2.Octave.Off"

								# PRIORITY 0: band beyond the MIDI range -> dark
								if target_band >= band_count:
									target_color = "DefaultButton.Disabled"
									self._grid_back_buffer[x][y] = target_color
									continue

								# PRIORITY 1: overflow indicators (edge rows)
								is_top_edge = (y == 0)
								is_bottom_edge = (y == 7)
								if is_top_edge and highest_note_band > max_visible_band:
									diff = highest_note_band - max_visible_band
									if diff >= 3:
										target_color = "StepSequencer2.Octave.OnAbove3"
									elif diff == 2:
										target_color = "StepSequencer2.Octave.OnAbove2"
									else:
										target_color = "StepSequencer2.Octave.OnAbove1"
								elif is_bottom_edge and (lowest_note_band < min_visible_band):
									diff = min_visible_band - lowest_note_band
									if diff >= 3:
										target_color = "StepSequencer2.Octave.OnBelow3"
									elif diff == 2:
										target_color = "StepSequencer2.Octave.OnBelow2"
									else:
										target_color = "StepSequencer2.Octave.OnBelow1"

								# PRIORITY 2: the cross centred on where we come from in
								# the note editor:
								#   horizontal arm = displayed pitch block / octave row
								#   vertical arm   = selected step-block column
								# Loop variant when the cell's block (column) is inside
								# the clip loop.
								elif target_band == displayed_band or is_selected_col:
									if has_note_here:
										target_color = ("StepSequencer2.Octave.OnSelectedBlockLoop" if is_loop_col
														else "StepSequencer2.Octave.OnSelectedBlock")
									else:
										target_color = ("StepSequencer2.Octave.OffSelectedBlockLoop" if is_loop_col
														else "StepSequencer2.Octave.OffSelectedBlock")

								# PRIORITY 3: everything outside the cross
								elif has_note_here:
									target_color = "StepSequencer2.Octave.On"
								elif is_loop_col:
									# empty block inside the loop
									target_color = "StepSequencer2.Octave.OffLoop"
								else:
									target_color = "StepSequencer2.Octave.Off"
								self._grid_back_buffer[x][y] = target_color
					elif self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR:
						# Safety check
						if self._editing_step is None:
							return

						# One slider per row: the notes of that pitch at the edited step
						for y in range(rows):
							pitch = self._pitch_for_row(y)
							levels = self._slider_levels(self._editing_step, pitch)
							if not levels:
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

							# Notes of different velocities are drawn as Mixed (red)
							self._draw_slider(
								levels, [(col, y) for col in range(8)],
								"StepSequencer2.Velocity.Off", "StepSequencer2.Velocity.Mixed")
						if not self._is_drum_view:
							self._draw_step_selector_row()
					elif self._mode == STEPSEQ_MODE_VERTICAL_VELOCITY:
						# One slider per step, showing only the notes visible in the view
						for col_x in range(8):
							idx = self._get_step_index(col_x)
							levels = self._slider_levels(idx)
							if levels:
								# Notes of different velocities are drawn as Mixed (red),
								# above the bar is BLACK
								self._draw_slider(
									levels, [(col_x, 7 - pad) for pad in range(8)],
									0, "StepSequencer2.Velocity.Mixed")
								continue

							# CASE: NO VISIBLE NOTES IN THIS STEP
							for row_y in range(8):
								if row_y <= 1:
									# Rows 0 and 1: BLACK
									self._grid_back_buffer[col_x][row_y] = 0
								else:
									# Rows 2 to 7: DIMMED
									self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Velocity.Dim"
					elif self._mode == STEPSEQ_MODE_VERTICAL_LENGTH:
						# One slider per step, showing only the notes visible in the view
						for col_x in range(8):
							idx = self._get_step_index(col_x)
							levels = self._slider_levels(idx)
							if levels:
								# Notes of different lengths are drawn as Mixed (red),
								# above the bar is BLACK
								self._draw_slider(
									levels, [(col_x, 7 - pad) for pad in range(8)],
									0, "StepSequencer2.Velocity.Mixed")
								continue

							# CASE: NO VISIBLE NOTES IN THIS STEP
							for row_y in range(8):
								if row_y <= 1:
									self._grid_back_buffer[col_x][row_y] = 0
								else:
									self._grid_back_buffer[col_x][row_y] = "StepSequencer2.Length.Dim"
					elif self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
						if self._editing_step is None:
							return

						# One slider per row: the notes of that pitch at the edited step
						for y in range(rows):
							pitch = self._pitch_for_row(y)
							levels = self._slider_levels(self._editing_step, pitch)
							if not levels:
								# No note here: draw dim grid background
								default_length_idx = 3
								for col in range(8):
									if col <= default_length_idx:
										self._grid_back_buffer[col][y] = "StepSequencer2.Length.Dim"
									else:
										self._grid_back_buffer[col][
											y] = "StepSequencer2.Length.Off"
								continue

							# Notes of different lengths are drawn as Mixed (red)
							self._draw_slider(
								levels, [(col, y) for col in range(8)],
								"StepSequencer2.Length.Dim", "StepSequencer2.Length.Mixed")
						if not self._is_drum_view:
							self._draw_step_selector_row()

				# --- METRONOME ---
				# playing notes (ONLY current visible page and Normal Mode)
				if self._playhead != None and self._mode == STEPSEQ_MODE_NOTES:
					try:
						if hasattr(self, 'resolution_beats') and callable(getattr(self, 'resolution_beats')):
							res = self.resolution_beats
						elif hasattr(self, '_resolution'):
							res = max(0.001, float(self._resolution))
						elif hasattr(self._step_sequencer, '_resolution'):
							res = max(0.001, float(self._step_sequencer._resolution))
						else:
							res = 0.25

						# Double-check clamp (extra safety)
						if res <= 0:
							res = 0.25
					except Exception as e:
						# Ultimate fallback if anything goes wrong
						if DEBUG_LOGGING:
							self._control_surface.log_message(f"[METRONOME_ERROR] Resolution error: {e}")
						res = 0.25
					play_position = int(self._playhead / res)
					play_x_position = play_position % 8
					page = int(play_position / 8)
					if page == effective_page:
						if self._is_drum_view:
							metronome_color = DRUM_METRONOME_NO_NOTE
							play_color = DRUM_METRONOME_NOTE
						else:
							metronome_color = "StepSequencer2.NoteEditor.MetronomeInPage"
							play_color = "StepSequencer2.NoteEditor.PlayInPage"

						# full column metronome
						for y in range(rows):
							self._grid_back_buffer[play_x_position][y] = metronome_color
						for y in range(rows):
							if self._notes_pitches[play_position * rows + (rows - 1) - y] == 1:
								self._grid_back_buffer[play_x_position][y] = play_color
			else:
				for x in range(8):
					for y in range(rows):
						self._grid_back_buffer[x][y] = "DefaultButton.Disabled"
		if self._matrix != None:
			for x in range(8):
				for y in range(8):
					if y == 7 and not self.uses_bottom_row():
						continue
					button = self._matrix.get_button(x, y)
					button.set_light(self._grid_back_buffer[x][y])
			self._force_update = False

	def _matrix_value(self, value, sender):
		x = -1
		y = -1
		for xx in range(8):
			for yy in range(8):
				if self._matrix.get_button(xx, yy) == sender:
					x = xx
					y = yy

		# --- ROW 7 IN HORIZONTAL EDITORS ---
		# Drumrack: row 7 edits the lowest note in view, like the other rows.
		# Melodic: the notes end at row 6, row 7 selects the step to edit.
		if (self._mode in (STEPSEQ_MODE_STEP_VELOCITY_EDITOR, STEPSEQ_MODE_STEP_LENGTH_EDITOR)
				and y == 7 and not self._is_drum_view):
			if self.is_enabled() and ((value != 0) or (not sender.is_momentary())):
				self._select_editing_step(x)
			return
		effective_page = self._get_effective_page()
		if self.is_enabled() and self._matrix != None:
			if self._clip == None:
				self._step_sequencer.create_clip()
			else:
				start = int(self._clip.loop_start / self.resolution_beats)
				end = int(self._clip.loop_end / self.resolution_beats)
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
					idx = self._get_step_index(x)
					self._editing_step = idx
					self._velocity_wait_animation = False
					self._pending_velocity_editor = False
					self.set_mode(STEPSEQ_MODE_STEP_VELOCITY_EDITOR)
					return

				# If we are currently animating (waiting for user selection),
				# pressing a grid button stops the animation and enters Horizontal Editor immediately.
				if self._length_wait_animation and ((value != 0) or (not sender.is_momentary())):
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
					return
				if self._pending_length_editor:
					idx = self._get_step_index(x)
					self._editing_step = idx
					self._pending_length_editor = False
					self._length_wait_animation = False
					self.set_mode(STEPSEQ_MODE_STEP_LENGTH_EDITOR)
					return

				# --- NAVIGATION OVERVIEW (press AND release are relevant) ---
				# Single tap  -> select pitch band + block, back to notes view
				# Hold + tap  -> set clip loop from first held block to second block
				if self._mode == STEPSEQ_MODE_NAV_OVERVIEW:
					self._handle_nav_overview_button(x, y, value, sender)
					return

				# --- VELOCITY / LENGTH SLIDERS: RELEASING THE HELD PAD ---
				if value == 0 and sender.is_momentary():
					if self._mode in (STEPSEQ_MODE_STEP_VELOCITY_EDITOR, STEPSEQ_MODE_VERTICAL_VELOCITY,
									STEPSEQ_MODE_STEP_LENGTH_EDITOR, STEPSEQ_MODE_VERTICAL_LENGTH):
						self._release_slider_hold(x, y)
					elif self._mode == STEPSEQ_MODE_NOTES:
						self._release_note_pad(x, y)
					return

				# --- MAIN BUTTON PRESS HANDLING ---
				allow_row7 = (self._is_drumrack_layout or
				              self._mode in (STEPSEQ_MODE_VERTICAL_VELOCITY, STEPSEQ_MODE_VERTICAL_LENGTH, STEPSEQ_MODE_NAV_OVERVIEW))
				rows = self._note_rows
				if ((value != 0) or (not sender.is_momentary())):
					if y == 7 and not allow_row7:
						return
					idx = self._get_step_index(x)
					# A pad showing a note acts on its release (it can be held as a zoom anchor)
					if self._mode == STEPSEQ_MODE_NOTES and self._press_note_pad(x, y, idx):
						return
					if self._mode == STEPSEQ_MODE_NOTES:
						# Toggle note at grid position
						if self._notes_pitches[(idx) * rows + (rows - 1) - y] == 1:
							self._notes_pitches[(idx) * rows + (rows - 1) - y] = 0
						else:
							# Clear step if monophonic mode
							if self._is_monophonic:
								for yy in range(7):
									self._notes_pitches[(idx) * rows + (rows - 1) - yy] = 0
							self._notes_pitches[(idx) * rows + (rows - 1) - yy] = 1
					elif self._mode == STEPSEQ_MODE_STEP_VELOCITY_EDITOR:
						# HORIZONTAL VELOCITY MODE WITH DOUBLE-CLICK DELETE

						# Use a timestamp variable name to avoid conflict if 'time' is used locally
						current_ts = time.time()
						pitch = self._pitch_for_row(y)
						step_idx = self._editing_step

						# --- TWO PADS: hold a pad and tap the pad right/left of it (fine
						# adjustment) or a pad on another row (linear ramp over the pitches) ---
						if self._slider_two_pad_gesture(x, y):
							return

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
							self._slider_hold = None
							# --- DELETE OPERATION (DELETE LAST NOTE IN STEP) ---
							step_idx = self._editing_step
							start_time = step_idx * self.resolution_beats
							end_time = start_time + self.resolution_beats
							notes = list(self._note_cache)

							# Find the note with the HIGHEST time value matching the pitch and step
							target_index = -1
							max_note_time = -1.0
							for i, note in enumerate(notes):
								note_p, note_t, note_l, note_v, note_m = note
								if (note_p == pitch and
										start_time <= note_t < end_time):
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
							if not self._press_slider_pad(x, y, step_idx, pitch, velocity, sender):
								self._add_note_at_step(step_idx, pitch, velocity)
								self._update_matrix()
							return
					elif self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
						# HORIZONTAL LENGTH MODE WITH DOUBLE-CLICK DELETE

						current_ts = time.time()
						pitch = self._pitch_for_row(y)
						step_idx = self._editing_step

						# --- TWO PADS: hold a pad and tap the pad right/left of it (fine
						# adjustment) or a pad on another row (linear ramp over the pitches) ---
						if self._slider_two_pad_gesture(x, y):
							return

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
							self._slider_hold = None
							# --- DELETE OPERATION ---
							step_idx = self._editing_step
							start_time = step_idx * self.resolution_beats
							end_time = start_time + self.resolution_beats
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
							target_length = self._length_map[len_bucket] * self.resolution_beats / 4.0
							start_time = step_idx * self.resolution_beats
							end_time = start_time + self.resolution_beats

							# Double Click Check (Delete Logic) - Keep your existing double click logic here

							# Single Click Logic: update the note's length only (preserves velocity)
							if not self._press_slider_pad(x, y, step_idx, pitch, target_length, sender):
								new_note = (pitch, start_time, target_length, self._note_velocity, False)
								notes = list(self._note_cache)
								notes.append(new_note)
								self._write_note_cache_to_clip(notes)
								self._note_cache = tuple(notes)
								self._parse_notes()
								self._update_matrix()
							return
					elif self._mode == STEPSEQ_MODE_VERTICAL_VELOCITY:
						# VERTICAL VELOCITY MODE - INSTANT EXECUTE WITH REVERT ON DOUBLE CLICK
						# The slider only controls the notes visible in the current view.
						current_ts = time.time()
						step_idx = idx
						vel_bucket = 7 - y
						if vel_bucket < 0: vel_bucket = 0
						if vel_bucket > 7: vel_bucket = 7
						target_velocity = self._velocity_map[vel_bucket]
						start_time = step_idx * self.resolution_beats
						end_time = start_time + self.resolution_beats

						# --- TWO PADS: hold a pad and tap the pad above/below it (fine
						# adjustment) or a pad on another column (linear ramp over the steps) ---
						if self._slider_two_pad_gesture(x, y):
							return

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
							self._slider_hold = None

							# 1. REVERT to the saved OLD state
							if self._pending_revert_data is not None:
								self._write_note_cache_to_clip(self._pending_revert_data)
								self._note_cache = tuple(self._pending_revert_data)
								# CRITICAL: Refresh display data from cache
								self._parse_notes()
								self._pending_revert_data = None

							# 2. PERFORM DELETE on the NOW-CORRECTED cache:
							#    a visible note whose last pad is the tapped pad
							pad_values = self._slider_pad_values()
							current_notes = list(self._note_cache)
							target_index = -1
							max_note_time = -1.0
							for i, note in enumerate(current_notes):
								note_p, note_t, note_l, note_v, note_m = note
								if (start_time <= note_t < end_time and
										self._visible_row_for_pitch(note_p) >= 0 and
										self._slider_level_pad(self._slider_level(note_v, pad_values)) == vel_bucket):
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
							# Saves the state for a revert, then applies the change (visible notes only)
							self._press_slider_pad(x, y, step_idx, None, target_velocity, sender)
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
						target_length = self._length_map[len_bucket] * self.resolution_beats / 4.0
						start_time = step_idx * self.resolution_beats
						end_time = start_time + self.resolution_beats

						# --- TWO PADS: hold a pad and tap the pad above/below it (fine
						# adjustment) or a pad on another column (linear ramp over the steps) ---
						if self._slider_two_pad_gesture(x, y):
							return

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
							self._slider_hold = None

							# 1. Revert state if needed (optional safety)
							if self._pending_revert_data is not None:
								self._write_note_cache_to_clip(self._pending_revert_data)
								self._note_cache = tuple(self._pending_revert_data)
								self._parse_notes()
								self._pending_revert_data = None

							# 2. Find the target note to delete
							# Logic: Delete the note in this step that matches the target length bucket
							#        AND has the HIGHEST time value (latest creation order).

							pad_values = self._slider_pad_values()
							current_notes = list(self._note_cache)
							target_index = -1
							max_note_time = -1.0
							for i, note in enumerate(current_notes):
								note_p, note_t, note_l, note_v, note_m = note

								# Check if note is in this step and visible in the view
								if start_time <= note_t < end_time and self._visible_row_for_pitch(note_p) >= 0:
									# Check if its last lit pad is the clicked row's bucket
									if self._slider_level_pad(self._slider_level(note_l, pad_values)) == len_bucket:
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
							# --- HANDLE SINGLE CLICK (SET LENGTH FOR ALL VISIBLE NOTES IN STEP) ---
							# Saves the state for a revert, then applies the change
							self._press_slider_pad(x, y, step_idx, None, target_length, sender)
							return
					elif self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
						current_ts = time.time()
						pitch = self._pitch_for_row(y)
						step_idx = self._editing_step

						# Calculate target length from column X (mirroring velocity's use of x)
						len_bucket = x  # Column 0-7 maps directly to length_map index
						if len_bucket > 7: len_bucket = 7
						target_length = self._length_map[len_bucket] * self.resolution_beats / 4.0
						start_time = step_idx * self.resolution_beats
						end_time = start_time + self.resolution_beats
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
								# CREATE NEW NOTE WITH THE CC VELOCITY AND TARGET LENGTH
								new_note = (pitch, start_time, target_length, self._note_velocity, False)
								notes = list(self._note_cache)
								notes.append(new_note)
								self._write_note_cache_to_clip(notes)
								self._note_cache = tuple(notes)
							else:
								# UPDATE EXISTING NOTE'S LENGTH ONLY
								self._set_length_for_pitch_at_step(step_idx, pitch, target_length)
							self._parse_notes()
							self._update_matrix()
							return

					# Default handling for other modes (Copy/Paste, etc.)
					self._update_matrix()
					if self._mode == STEPSEQ_MODE_NOTES:
						self._toggle_note_at_grid_position(idx, y)
						return
					else:
						self._update_clip_notes()

	# --- HELD NOTE PADS (ZOOM ANCHOR) ---
	def _press_note_pad(self, x, y, idx):
		"""
		Notes view: remember the pad as held, so the resolution button can
		zoom around it (see take_zoom_anchor). A pad that shows a note is
		toggled (its note deleted) on release instead of on press, and not
		at all if the resolution button is used while it is held.
		Returns True if the toggle waits for the release.
		"""
		row_idx = (self._note_rows - 1) - y
		pad_notes = [n for n in self._get_notes_at_step(idx)
		             if self._visible_row_for_pitch(n[0]) == row_idx]
		row_pitch = self._row_pitch(row_idx)
		step_start = idx * self.resolution_beats
		self._held_note_pads = [p for p in self._held_note_pads if p['pos'] != (x, y)]
		self._held_note_pads.append({
			'pos': (x, y),
			'idx': idx,
			'pitch': row_pitch,
			# Where the pad's notes are, in absolute terms (see _held_pad_notes)
			'start': step_start,
			'end': step_start + self.resolution_beats,
			'pitches': set(n[0] for n in pad_notes) | {row_pitch},
			# Zoom anchor: the pad's first note, else the step start
			'time': min(n[1] for n in pad_notes) if pad_notes else step_start,
			'toggle_on_release': bool(pad_notes),
		})
		return bool(pad_notes)

	def _release_note_pad(self, x, y):
		for pad in self._held_note_pads:
			if pad['pos'] == (x, y):
				self._held_note_pads.remove(pad)
				if pad['toggle_on_release']:
					self._toggle_note(pad['idx'], pad['pitch'])
				return

	def _take_held_note_pads(self):
		"""
		Notes-view pads still held, in press order, for a gesture made
		while holding them. They are then left alone on release: they were
		used for the gesture, not to delete their notes.
		"""
		held = [p for p in self._held_note_pads
		        if self._matrix.get_button(p['pos'][0], p['pos'][1]).is_pressed()]
		for pad in held:
			pad['toggle_on_release'] = False
		return held

	def _held_pad_notes(self, pad):
		"""
		Current notes of a held pad, all parameters included. Read from the
		clip notes when asked, so a note added by the press is found too.
		"""
		return [tuple(n) for n in self._note_cache
		        if pad['start'] <= n[1] < pad['end'] and n[0] in pad['pitches']]

	def take_zoom_anchor(self):
		"""
		Time (beats) to zoom around: the first notes-view pad still held,
		or the notes being moved in the navigation view; else None.
		"""
		if self._nav_move is not None:
			return self._nav_move['anchor_time']
		held = self._take_held_note_pads()
		return held[0]['time'] if held else None

	def _capture_held_notes(self):
		"""
		The notes of the held notes-view pads, to be moved through the
		navigation view, or None if no pad (with notes) is held. The
		anchor is the first note of the first pad held: its block and the
		octave / pitch block in view decide where the notes land.
		"""
		held = self._take_held_note_pads()
		notes = []
		for pad in held:
			for note in self._held_pad_notes(pad):
				if note not in notes:
					notes.append(note)
		if not notes:
			return None
		first_pad_notes = self._held_pad_notes(held[0]) or notes
		return {
			'notes': notes,
			'anchor_time': min(n[1] for n in first_pad_notes),
			'display_octave': self._display_octave,
			'focus_pitch': self._focus_pitch if self._is_focus_view else None,
		}

	# --- FOCUS MODE (melodic notes view) ---
	def handle_focus_button(self):
		"""
		Scale button pressed. Melodic notes view only:
		- a note pad held: focus mode around that pad's note
		- no pad held, in focus mode: back to the normal notes view
		Returns True if the press was used here (no scale editor then).
		"""
		if self._is_drum_view or self._mode != STEPSEQ_MODE_NOTES or self._clip is None:
			return False
		held = self._take_held_note_pads()
		if held:
			pitch = self._held_pad_focus_pitch(held[0])
			if pitch is None:
				self._control_surface.show_message("Focus: no note on this pad")
			else:
				self.set_focus_pitch(pitch)
			return True
		if self._is_focus_view:
			self.set_focus_pitch(None)
			return True
		return False

	def _held_pad_focus_pitch(self, pad):
		"""
		Pitch to focus on for a held pad: its first note (the one closest to
		the row's pitch if several start together), else the row's pitch.
		None if the row is outside the MIDI range.
		"""
		notes = self._held_pad_notes(pad)
		if notes:
			return min(notes, key=lambda n: (n[1], abs(n[0] - pad['pitch'])))[0]
		if 0 <= pad['pitch'] <= 127:
			return pad['pitch']
		return None

	def set_focus_pitch(self, pitch):
		"""
		Focus mode: the rows show the semitones around pitch, which is on LP
		row FOCUS_ROW (see _row_pitch). None leaves focus mode. The scale
		view's octave (_display_octave) is kept for when focus mode ends.
		"""
		self._focus_pitch = pitch
		self._parse_notes()
		self._force_update = True
		self.update()
		ss = self._step_sequencer
		ss._update_buttons()
		ss._update_OSD()
		if pitch is None:
			self._control_surface.show_message("Focus mode off")
		else:
			self._control_surface.show_message(
				"Focus mode: %s (tap Scale to leave)" % self._pitch_name(pitch))

	def _pitch_name(self, pitch):
		"""Note name of a MIDI pitch, e.g. 36 -> C1."""
		return "%s%d" % (KEY_NAMES[pitch % 12], pitch // 12 - 2)

	def _focus_pad_color(self, pitch, notes, scale_notes):
		"""
		Focus mode colour of a pad whose row shows pitch; notes = the notes
		of that pitch in the pad's step (see FOCUS_COLORS_IN_SCALE).
		"""
		if pitch % 12 in scale_notes:
			no_note, on_step, off_step, several = FOCUS_COLORS_IN_SCALE
		else:
			no_note, on_step, off_step, several = FOCUS_COLORS_OUT_SCALE
		if not notes:
			return no_note
		if len(notes) > 1:
			return several
		if self._is_note_on_grid(notes[0][1], self.resolution_beats):
			return on_step
		return off_step

	def _get_row_for_pitch(self, midi_note):
		"""
		Find which grid row (0-6) corresponds closest to this MIDI note.
		Drumrack layout and focus mode: exact mapping (one semitone per row).
		Returns -1 if no reasonable mapping exists.
		"""
		rows = self._note_rows
		if self._is_drum_view or self._is_focus_view:
			offset = int(midi_note) - self._row_pitch(0)
			if 0 <= offset < rows:
				return offset
			return -1

		# Get all possible pitches on the grid
		grid_pitches = []
		for row_idx in range(rows):
			pitch = self._row_pitch(row_idx)
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

	def _visible_row_for_pitch(self, midi_note):
		"""
		Row on which the note grid draws this pitch, or -1 when notes of this
		pitch are not visible. Melodic notes are drawn on the closest row, but
		only when they are in the same octave as that row's pitch.
		"""
		row_idx = self._get_row_for_pitch(midi_note)
		if row_idx < 0 or self._is_drum_view or self._is_focus_view:
			return row_idx
		if int(midi_note / 12) != int(self._row_pitch(row_idx) / 12):
			return -1
		return row_idx

	def _get_visible_notes_at_step(self, idx):
		"""Notes of a step that the note grid shows in the current view."""
		return [note for note in self._get_notes_at_step(idx)
				if self._visible_row_for_pitch(note[0]) >= 0]

	def _is_note_on_grid(self, note_time, resolution):
		tolerance = 0.001
		remainder = abs(note_time % resolution)
		return remainder < tolerance or remainder > (resolution - tolerance)

	def _add_note_at_step(self, idx, pitch, velocity):
		start_time = idx * self.resolution_beats
		end_time = start_time + self.resolution_beats
		notes = list(self._note_cache)

		# Don't create duplicates
		for note in notes:
			if note[0] == pitch and start_time <= note[1] < end_time:
				return
		notes.append(
			(pitch, start_time, self.resolution_beats, velocity, False)
		)
		self._write_note_cache_to_clip(notes)
		self._note_cache = tuple(notes)

	def _pitch_for_row(self, y):
		rows = self._note_rows
		return self._row_pitch(rows - 1 - y)

	def _get_step_index(self, x):
		return x + 8 * self._get_effective_page()

	def _select_editing_step(self, x):
		"""Melodic horizontal editors: a press on row 7 edits that step."""
		self._editing_step = self._get_step_index(x)
		self._slider_hold = None
		self._last_velocity_press_pos = None
		self._last_length_press_pos = None
		name = "Length" if self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR else "Velocity"
		self._control_surface.show_message("Horizontal %s Step %d" % (name, self._editing_step))
		self._update_matrix()
		self._step_sequencer._update_OSD()

	def _draw_step_selector_row(self):
		"""
		Melodic horizontal editors: row 7 shows the steps of the page, the
		edited step in the playhead colour, the other steps in the colours of
		the editor (On with notes in view, Dim without).
		"""
		if self._mode == STEPSEQ_MODE_STEP_LENGTH_EDITOR:
			with_notes, empty = "StepSequencer2.Length.On", "StepSequencer2.Length.Dim"
		else:
			with_notes, empty = "StepSequencer2.Velocity.On", "StepSequencer2.Velocity.Dim"
		for x in range(8):
			idx = self._get_step_index(x)
			has_notes = len(self._get_visible_notes_at_step(idx)) > 0
			if idx == self._editing_step:
				if has_notes:
					color = "StepSequencer2.NoteEditor.PlayInPage"
				else:
					color = "StepSequencer2.NoteEditor.MetronomeInPage"
			elif has_notes:
				color = with_notes
			else:
				color = empty
			self._grid_back_buffer[x][7] = color

	# ------------------------------------------------------------------
	# VELOCITY / LENGTH SLIDERS
	# A slider controls the notes of one pitch at a step (horizontal editors)
	# or, with pitch None, the notes of a step visible in the current view
	# (vertical editors). The current mode tells velocity from length.
	# Values are handled as levels: level 0 is pad 0, pad n >= 1 covers
	# levels 5n-4 .. 5n. Levels 5n-4 .. 5n-1 are the 4 values between pad n-1
	# and pad n (Sub1 .. Sub4), level 5n is the value of pad n itself.
	# ------------------------------------------------------------------
	@property
	def _slider_is_length(self):
		return self._mode in (STEPSEQ_MODE_STEP_LENGTH_EDITOR, STEPSEQ_MODE_VERTICAL_LENGTH)

	@property
	def _slider_is_vertical(self):
		return self._mode in (STEPSEQ_MODE_VERTICAL_VELOCITY, STEPSEQ_MODE_VERTICAL_LENGTH)

	def _slider_pad_values(self):
		"""Value of each slider pad: a velocity, or a length in beats."""
		if self._slider_is_length:
			return [length * self.resolution_beats / 4.0 for length in self._length_map]
		return self._velocity_map

	def _slider_note_value(self, note):
		return note[2] if self._slider_is_length else note[3]

	def _slider_max_level(self):
		return (len(self._slider_pad_values()) - 1) * SLIDER_SUBDIVISIONS

	def _slider_level_pad(self, level):
		"""Last lit pad of the slider for this level."""
		return (level + SLIDER_SUBDIVISIONS - 1) // SLIDER_SUBDIVISIONS

	def _slider_level_sub(self, level):
		"""1 .. 4: value between two pads (Sub1 .. Sub4), 5: value of the pad itself."""
		return (level - 1) % SLIDER_SUBDIVISIONS + 1

	def _slider_value_of_level(self, level):
		pad_values = self._slider_pad_values()
		pad = self._slider_level_pad(level)
		sub = self._slider_level_sub(level)
		if pad == 0 or sub == SLIDER_SUBDIVISIONS:
			value = pad_values[pad]
		else:
			low = pad_values[pad - 1]
			value = low + (pad_values[pad] - low) * sub / float(SLIDER_SUBDIVISIONS)
		if self._slider_is_length:
			return value
		return int(value + 0.5)

	def _slider_level(self, value, pad_values):
		"""Level closest to a value (also for values edited in Live)."""
		if value <= pad_values[0]:
			return 0
		for pad in range(1, len(pad_values)):
			if value <= pad_values[pad]:
				low = pad_values[pad - 1]
				fraction = (value - low) / float(pad_values[pad] - low)
				return (pad - 1) * SLIDER_SUBDIVISIONS + int(fraction * SLIDER_SUBDIVISIONS + 0.5)
		return self._slider_max_level()

	def _slider_levels(self, idx, pitch=None):
		"""Levels of the notes a slider controls."""
		if pitch is None:
			notes = self._get_visible_notes_at_step(idx)
		else:
			notes = [note for note in self._get_notes_at_step(idx) if note[0] == pitch]
		pad_values = self._slider_pad_values()
		return [self._slider_level(self._slider_note_value(note), pad_values) for note in notes]

	def _slider_last_pad(self, idx, pitch=None):
		"""Last lit pad of a slider, or -1 when it controls no notes."""
		levels = self._slider_levels(idx, pitch)
		if not levels:
			return -1
		return self._slider_level_pad(max(levels))

	def _draw_slider(self, levels, pads, off_color, mixed_color):
		"""
		Slider colours for the levels of its notes; pads[i] is the (x, y) of
		pad i. A last lit pad between two pad values shows Sub1 .. Sub4, the
		other lit pads show Mixed when the notes have different values.
		"""
		if self._slider_is_length:
			on_color, sub_colors = "StepSequencer2.Length.On", LENGTH_SUB_COLORS
		else:
			on_color, sub_colors = "StepSequencer2.Velocity.On", VELOCITY_SUB_COLORS
		top_level = max(levels)
		last_pad = self._slider_level_pad(top_level)
		sub = self._slider_level_sub(top_level)
		is_mixed = len(set(levels)) > 1
		for pad, (x, y) in enumerate(pads):
			if pad > last_pad:
				color = off_color
			elif pad == last_pad and sub < SLIDER_SUBDIVISIONS:
				color = sub_colors[sub - 1]
			elif is_mixed:
				color = mixed_color
			else:
				color = on_color
			self._grid_back_buffer[x][y] = color

	def _set_step_slider_value(self, idx, value, pitch=None):
		"""Set the velocity/length of the notes a slider controls. True if changed."""
		return self._apply_slider_values({(idx, pitch): value})

	def _apply_slider_values(self, values):
		"""
		Set velocities/lengths for several sliders in one clip write.
		values: {(step, pitch): value}, pitch None meaning the notes of the step
		visible in the current view. True if a note changed.
		"""
		field = 2 if self._slider_is_length else 3
		notes = list(self._note_cache)
		changed = False
		for (idx, pitch), value in values.items():
			start_time = idx * self.resolution_beats
			end_time = start_time + self.resolution_beats
			for i, note in enumerate(notes):
				note_pitch, note_time = note[0], note[1]
				if not (start_time <= note_time < end_time):
					continue
				if pitch is None:
					if self._visible_row_for_pitch(note_pitch) < 0:
						continue
				elif note_pitch != pitch:
					continue
				if abs(note[field] - value) > 0.0001:
					new_note = list(note)
					new_note[field] = value
					notes[i] = tuple(new_note)
					changed = True
		if changed:
			self._write_note_cache_to_clip(notes)
			self._note_cache = tuple(notes)
			self._parse_notes()
		return changed

	def _slider_value_message(self, value):
		if self._slider_is_length:
			return "%g beats" % round(value, 4)
		return "%d" % value

	def _slider_two_pad_gesture(self, x, y):
		"""
		Press on (x, y) while another slider pad is held. The pad next to the
		held one on its slider: fine adjustment. A pad on another step
		(vertical editors) or another pitch (horizontal editors): linear ramp.
		While the ramp's end pad is held too, the pad next to it fine-tunes the
		end of the ramp, the pad next to the start pad its start.
		Returns True when the press was one of these gestures.
		"""
		hold = self._slider_hold
		if hold is None:
			return False
		ramp_end = hold['ramp_end']
		end_direction = self._slider_fine_direction(ramp_end, x, y)
		direction = self._slider_fine_direction(hold, x, y)
		if end_direction != 0:
			ramp_end['value'] = self._nudge_value(ramp_end['value'], end_direction)
			self._apply_ramp()
		elif direction != 0:
			if ramp_end is not None:
				hold['anchor'] = self._nudge_value(hold['anchor'], direction)
				self._apply_ramp()
			else:
				self._nudge_slider(direction)
		elif (x != hold['x']) if self._slider_is_vertical else (y != hold['y']):
			pad = (7 - y) if self._slider_is_vertical else x
			hold['ramp_end'] = {'x': x, 'y': y, 'value': self._slider_pad_values()[pad]}
			self._apply_ramp()
		else:
			return False
		# Taps of a gesture never count as a double click
		self._last_velocity_press_pos = None
		self._last_length_press_pos = None
		return True

	def _slider_fine_direction(self, pad, x, y):
		"""
		+1 / -1 when (x, y) is the pad above / below a held pad of a vertical
		slider (right / left of it in the horizontal editors), 0 otherwise or
		when no pad is given.
		"""
		if pad is None:
			return 0
		if self._slider_is_vertical:
			if x == pad['x'] and abs(y - pad['y']) == 1:
				return 1 if y < pad['y'] else -1
		elif y == pad['y'] and abs(x - pad['x']) == 1:
			return 1 if x > pad['x'] else -1
		return 0

	def _nudge_value(self, value, direction):
		"""Value one subdivision above (+1) or below (-1) a value."""
		level = self._slider_level(value, self._slider_pad_values()) + direction
		return self._slider_value_of_level(max(0, min(self._slider_max_level(), level)))

	def _nudge_slider(self, direction):
		"""Move the held slider one subdivision up or down."""
		hold = self._slider_hold
		# The press on the held pad no longer sets its own value
		hold['value'] = None
		levels = self._slider_levels(hold['step'], hold['pitch'])
		if not levels:
			return
		level = max(0, min(self._slider_max_level(), max(levels) + direction))
		value = self._slider_value_of_level(level)
		# A ramp started from the held pad now starts from this value
		hold['anchor'] = value
		if self._set_step_slider_value(hold['step'], value, hold['pitch']):
			self._update_matrix()
		name = "Length" if self._slider_is_length else "Velocity"
		self._control_surface.show_message("%s %s" % (name, self._slider_value_message(value)))

	def _apply_ramp(self):
		"""
		Spread values linearly from the held pad to the ramp's end pad: over
		the steps between the two columns (vertical editors), or the pitches
		between the two rows at the edited step (horizontal editors), both
		included. Only the notes present in the grid change.
		"""
		hold = self._slider_hold
		ramp_end = hold['ramp_end']
		# The held pad's value is applied as the start of the ramp
		hold['value'] = None
		if self._slider_is_vertical:
			first, last = hold['x'], ramp_end['x']
		else:
			first, last = hold['y'], ramp_end['y']
		start_value, end_value = hold['anchor'], ramp_end['value']
		direction = 1 if last > first else -1
		values = {}
		for position in range(first, last + direction, direction):
			value = start_value + (end_value - start_value) * (position - first) / float(last - first)
			if not self._slider_is_length:
				value = int(value + 0.5)
			if self._slider_is_vertical:
				values[(self._get_step_index(position), None)] = value
			else:
				values[(hold['step'], self._pitch_for_row(position))] = value
		if self._apply_slider_values(values):
			self._update_matrix()
		name = "Length" if self._slider_is_length else "Velocity"
		self._control_surface.show_message("%s ramp %s -> %s" % (
			name, self._slider_value_message(start_value), self._slider_value_message(end_value)))

	def _release_slider_hold(self, x, y):
		"""
		Releasing the held pad applies its value, unless it was nudged or
		started a ramp. Releasing the ramp's end pad ends the ramp gesture.
		"""
		hold = self._slider_hold
		if hold is None:
			return
		ramp_end = hold['ramp_end']
		if ramp_end is not None and ramp_end['x'] == x and ramp_end['y'] == y:
			hold['ramp_end'] = None
			return
		if hold['x'] != x or hold['y'] != y:
			return
		self._slider_hold = None
		if hold['value'] is None:
			return
		if self._set_step_slider_value(hold['step'], hold['value'], hold['pitch']):
			self._update_matrix()

	def _press_slider_pad(self, x, y, step_idx, pitch, value, sender):
		"""
		Single press on a slider pad: sets the value of the notes it controls.
		Returns False when the slider controls no notes (nothing done).
		The last lit pad is set on release instead, so that holding it for
		fine adjustment keeps the current in-between value.
		"""
		hold = None
		if sender.is_momentary():
			# 'value': value set on release; 'anchor': start value of a ramp;
			# 'ramp_end': the ramp's end pad while it is held
			hold = {'x': x, 'y': y, 'step': step_idx, 'pitch': pitch,
					'value': None, 'anchor': value, 'ramp_end': None}
		self._slider_hold = hold
		if pitch is None:
			# Vertical editors: a double tap reverts this press
			self._pending_revert_data = list(self._note_cache)
		last_pad = self._slider_last_pad(step_idx, pitch)
		if last_pad == -1:
			return False
		pad = (7 - y) if self._slider_is_vertical else x
		if hold is not None and pad == last_pad:
			hold['value'] = value
			return True
		if self._set_step_slider_value(step_idx, value, pitch):
			self._update_matrix()
		return True

	def _set_length_for_pitch_at_step(self, idx, pitch, target_length):
		start_time = idx * self.resolution_beats
		end_time = start_time + self.resolution_beats
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
		if not self._velocity_wait_animation:
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
		clip_slot = self.song().view.highlighted_clip_slot
		# Toggle released while no clip is in view (e.g. the clip was just
		# deleted by Toggle + double-tap Copy): nothing to suppress any more.
		if value == 0 and (clip_slot is None or not clip_slot.has_clip):
			if hasattr(self, '_step_sequencer') and self._step_sequencer:
				self._step_sequencer._suppress_toggle_release = False
		if (self.is_enabled() and clip_slot is not None and clip_slot.has_clip):
			if hasattr(self, '_duplicate_pending') and self._duplicate_pending:
				# Toggle released before Copy: the gesture owns this release
				if value == 0 and hasattr(self, '_step_sequencer') and self._step_sequencer:
					self._step_sequencer._suppress_toggle_release = False
				return
			if (value == 0 and sender.is_momentary()) or (not sender.is_momentary()):

				# --- CHECK FOR DUPLICATE SUPPRESSION (from previous duplicate attempt) ---
				if hasattr(self, '_step_sequencer') and self._step_sequencer:
					if hasattr(self._step_sequencer, '_suppress_toggle_release') and \
							getattr(self._step_sequencer, '_suppress_toggle_release', False):
						self._step_sequencer._suppress_toggle_release = False
						return

				# Normal Toggle Logic
				if clip_slot.is_playing:
					clip_slot.stop()
					self._control_surface.show_message("clip stopped")
				else:
					clip_slot.fire()
					self._control_surface.show_message("clip playing")
				self._control_surface.schedule_message(1, self._update_clip_toggle_button)

			# --- DETECT TOGGLE PRESS WHILE COPY IS HELD ---
			elif value > 0:  # On Press of Toggle
				if hasattr(self, '_mode_copy_paste_button') and self._mode_copy_paste_button:
					if self._mode_copy_paste_button.is_pressed():
						# Both buttons held -> TRIGGER DUPLICATE IMMEDIATELY
						current_mode = self._mode
						if current_mode == STEPSEQ_MODE_NOTES:
							self._duplicate_clip_to_next_slot()
							if hasattr(self, '_step_sequencer') and self._step_sequencer:
								if not hasattr(self._step_sequencer, '_suppress_toggle_release'):
									self._step_sequencer._suppress_toggle_release = False
								self._step_sequencer._suppress_toggle_release = True

							# Prevent Copy release from doing anything
							self._skip_copy_release = True
							return


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
		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[SCENE_CHANGE] editor_id={id(self)} enabled={self.is_enabled()}"
			)
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

				# IMPORTANT:
				# During clip moves/reorders Live can notify us while
				# self._clip still points to the old clip (or is None),
				# even though the current slot already contains a clip.
				#
				# Register the listener on the clip belonging to the slot.
				# Do NOT assign it to self._clip here, because _clip is the
				# StepSequencer's active metadata clip and changing it here
				# interferes with clip selection/move handling.

				try:
					slot_clip = self._clip_slot.clip
					if slot_clip is not None:
						if not slot_clip.playing_status_has_listener(
								self._on_clip_playing_changed):
							slot_clip.add_playing_status_listener(
								self._on_clip_playing_changed)
				except Exception as e:
					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[CLIP_PLAYING_LISTENER_ERROR] {e}"
						)
		self._update_clip_toggle_button()

	def _on_clip_playing_changed(self):
		self._update_clip_toggle_button()

	def _on_clip_triggered_changed(self):
		self._update_clip_toggle_button()

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
				self._is_mute_shifted = True
				self._is_velocity_shifted = True

# OCTAVES & BLOCKS
	def set_display_octave(self, octave):
		self._display_octave = octave
		self._parse_notes()
		self._force_update = True
		self.update()
		if not getattr(self, "_loading_clip", False):
			self.sync_clip_with_json()


	def _update_mode_notes_octaves_button(self):
		if self.is_enabled():
			if (self._mode_notes_octaves_button != None):
				if self._clip != None:
					self._mode_notes_octaves_button.set_on_off_values("StepSequencer2.Octave.On", "StepSequencer2.Octave.Dim")
					if self._mode == STEPSEQ_MODE_NAV_OVERVIEW:
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

	def _nav_range_message(self, prefix):
		"""OSD message describing which bands the 8 navigation rows show."""
		start = self._overview_start_octave
		end = min(start + NAV_VISIBLE_BANDS - 1, self._nav_band_count() - 1)
		if self._is_drum_view:
			low_midi = start * DRUM_BLOCK_SIZE
			high_midi = min(127, (end + 1) * DRUM_BLOCK_SIZE - 1)
			return prefix + "  (LP's grid displays pitch blocks [%d , %d] = MIDI %d-%d)" % (
				start, end, low_midi, high_midi)
		return prefix + "  (LP's grid displays [%d , %d])" % (start - 2, end - 2)

	def _mode_button_notes_octaves_value(self, value, sender):
		assert (self._mode_notes_octaves_button != None)
		assert (value in range(128))
		if self.is_enabled() and self._clip != None:
			if value != 0 and sender.is_momentary() and self._mode == STEPSEQ_MODE_NOTES:
				self._nav_button_press_in_notes()
			elif ((value == 0) and (sender.is_momentary())):
				now = time.time()
				current_mode = self._mode
				if current_mode == STEPSEQ_MODE_NAV_OVERVIEW:
					time_delta = now - self._last_notes_octaves_button_press

					# --- DOUBLE CLICK ---
					if time_delta < 0.3:
						# Clear the pending Up action
						self._scroll_pending_action = False

						# Execute Down immediately
						unit = "Pitch Block" if self._is_drum_view else "Octave"
						if self._overview_start_octave > 0:
							self._overview_start_octave -= 1
							msg = "%s Range Down" % unit
						else:
							msg = "Min %s Reached" % unit
						self._control_surface.show_message(self._nav_range_message(msg))
						self._force_update = True
						self.update()

					# --- SINGLE CLICK ---
					else:
						# Set flag and schedule Up action
						self._scroll_pending_action = True
						self._control_surface.schedule_message(3, self._execute_octave_up_scroll)
					self._last_notes_octaves_button_press = now
					return

				# --- NOTES VIEW: overview, move notes, or back (see _nav_button_release_in_notes) ---
				elif current_mode == STEPSEQ_MODE_NOTES:
					self._nav_button_release_in_notes()
					return
				else:
					self.set_mode(STEPSEQ_MODE_NOTES)
					self._control_surface.show_message("Notes Mode")
					self._step_sequencer._update_OSD()
					self.update()
		else:
			if self._mode_notes_octaves_button:
				self._mode_notes_octaves_button.set_light("DefaultButton.Disabled")

	def _nav_button_press_in_notes(self):
		"""
		Notes view, navigation button pressed. Pads held now have their
		notes moved (see _land_moved_notes); a press right after a tap is
		the second tap of a double tap. Both act on release.
		"""
		now = time.time()
		self._nav_button_double_tap = False
		self._nav_move_capture = None
		pending = self._nav_button_pending_tap
		self._nav_button_pending_tap = None
		if pending is not None:
			if (now - pending) < NAV_BUTTON_DOUBLE_TAP_WINDOW:
				self._nav_button_double_tap = True
			else:
				# Window over but the scheduler has not run the tap yet;
				# this press then belongs to the navigation view.
				self._enter_nav_overview()
			return
		self._nav_move_capture = self._capture_held_notes()

	def _nav_button_release_in_notes(self):
		"""
		Notes view, navigation button released:
		- note pads were held at the press: navigation view to move their notes
		- double tap: back to the previous position (see _go_back_to_previous_position)
		- tap: navigation view, once the double-tap window has elapsed
		"""
		move = self._nav_move_capture
		self._nav_move_capture = None
		if move is not None:
			self._enter_nav_overview(move)
		elif self._nav_button_double_tap:
			self._nav_button_double_tap = False
			self._go_back_to_previous_position()
		else:
			# First tap: wait to see if a second tap follows
			self._nav_button_pending_tap = time.time()
			self._control_surface.schedule_message(
				NAV_BUTTON_DOUBLE_TAP_TICKS, self._execute_pending_nav_button_tap)

	def _execute_pending_nav_button_tap(self):
		"""Run a navigation button tap that was not followed by a second one."""
		pending = self._nav_button_pending_tap
		if pending is None:
			return
		# Scheduler ticks are approximate: if called too early, try again.
		if (time.time() - pending) < NAV_BUTTON_DOUBLE_TAP_WINDOW:
			self._control_surface.schedule_message(1, self._execute_pending_nav_button_tap)
			return
		self._nav_button_pending_tap = None
		if not self.is_enabled() or self._clip is None or self._mode != STEPSEQ_MODE_NOTES:
			return
		self._enter_nav_overview()

	def _enter_nav_overview(self, move=None):
		"""Notes view -> navigation view; move = notes to move (see _capture_held_notes)."""
		if self._is_drum_view:
			# Drumrack: 16 pitch blocks, 8 visible.
			# Put the displayed block on the 4th row from the bottom.
			new_start = self._display_octave - 3
			new_start = max(0, min(self._nav_max_start(), new_start))
		else:
			# 1. Capture the octave we are currently viewing
			current_octave = self._display_octave

			# 2. Calculate a new start octave to ensure current_octave is visible in an 8-octave window.
			# We want: start <= current_octave <= start + 7
			# Let's try to center it: start = current_octave - 3 (puts current at index 3, i.e., 4th row from bottom)
			new_start = current_octave - 3

			# 3. Clamp to valid MIDI range (0 to 15 approx)
			# Ensure the whole window [new_start, new_start+7] doesn't go negative
			if new_start < 0:
				new_start = 0
			# Ensure the top of the window doesn't exceed max useful octave
			if new_start + 7 > 12:
				new_start = 3
		self._overview_start_octave = new_start

		# Reset the timer for double-click detection
		self._last_notes_octaves_button_press = 0.0
		self._scroll_pending_action = False

		# Set Mode (the notes being moved, if any, survive the mode change)
		self._nav_entry_position = self._current_position()
		self._nav_move = move
		self.set_mode(STEPSEQ_MODE_NAV_OVERVIEW)

		# Show message with the calculated range
		if move is not None:
			self._control_surface.show_message("Move %d note(s): tap the block and %s where they land" % (
				len(move['notes']), "pitch block" if self._is_drum_view else "octave"))
		elif self._is_drum_view:
			self._control_surface.show_message("Pitch Block Overview ")
		else:
			self._control_surface.show_message("Octave Overview ") #% (self._overview_start_octave, self._overview_start_octave + 7)
		self._step_sequencer._update_OSD()
		self.update()

	# --- VIEW POSITIONS ---
	def _current_position(self):
		"""What the notes view shows: resolution, block, octave / pitch block and focus pitch."""
		return (self._step_sequencer._resolution_index, self._page,
		        self._display_octave, self._is_drum_view,
		        self._focus_pitch if self._is_focus_view else None)

	def _go_back_to_previous_position(self):
		"""
		Back to where the notes view was before the last jump made from the
		navigation view (a move included). The position left becomes the
		previous one, so doing it again goes back to where the jump landed.
		"""
		previous = self._previous_position
		if previous is None or previous[3] != self._is_drum_view:
			self._control_surface.show_message("No previous position")
			return
		self._previous_position = self._current_position()
		self._go_to_position(previous)
		self._control_surface.show_message("Back to block %d, %s (%s)" % (
			self._page, self._nav_band_label(self._displayed_nav_band()),
			RESOLUTION_NAMES[previous[0]]))

	def _go_to_position(self, position):
		"""Show a position from _current_position (resolution, block, octave, focus)."""
		if position is None or position[3] != self._is_drum_view:
			return
		resolution_index, block, display_octave = position[:3]
		self._focus_pitch = position[4]
		ss = self._step_sequencer
		if resolution_index != ss._resolution_index:
			ss._resolution_index = resolution_index
			ss.set_resolution(RESOLUTION_MAP[resolution_index])
		self._select_block(block)
		self.set_display_octave(display_octave)
		ss._update_loop_selector()
		ss._update_buttons()

	def _execute_octave_up_scroll(self):
		"""
		Called 0.3s after a single click.
	 Checks if a double-click cancelled it.
		"""
		# If we are not in overview mode, do nothing
		if self._mode != STEPSEQ_MODE_NAV_OVERVIEW:
			self._scroll_pending_action = False
			return

		# If the flag was cleared (by a double-click), abort
		if not self._scroll_pending_action:
			return

		# Otherwise, execute Up
		# (melodic: 11 octaves -> max start 3 ; drumrack: 16 blocks -> max start 8)
		unit = "Pitch Block" if self._is_drum_view else "Octave"
		if self._overview_start_octave < self._nav_max_start():
			self._overview_start_octave += 1
			msg = "%s Range Up" % unit
		else:
			msg = "Max %s Reached" % unit
		self._control_surface.show_message(self._nav_range_message(msg))
		self._force_update = True
		self.update()

		# Reset flag
		self._scroll_pending_action = False

	def _handle_nav_overview_button(self, x, y, value, sender):
		"""
		Grid button handler for the navigation overview (both layouts).

		- Tap one button (press + release, nothing else pressed):
		  select that column's block and that row's octave / pitch block,
		  then return to the notes view.
		- Hold one button and press another one:
		  set the clip loop from the FIRST step of the lower block (column)
		  to the LAST step of the higher block (inclusive). Pressing the
		  current loop again restores the full clip loop (same as the
		  loop selector row in melodic mode). The view stays in overview.
		- Double tap one button:
		  set the clip loop on that single block (column), like a double
		  tap on row 7 in melodic mode. Double tapping the block that
		  already is the whole loop restores the full clip loop.
		  The view stays in overview.

		Because of the double tap, a single tap is executed only once the
		double-tap window has elapsed without a second tap.

		While notes are being moved (see _land_moved_notes), a press lands
		them at once and the gestures above are off.
		"""
		is_momentary = sender.is_momentary()
		is_press = (value != 0) or (not is_momentary)

		if self._nav_move is not None:
			if is_press:
				self._land_moved_notes(x, y)
			return

		# Non-momentary (toggle) buttons give no usable release: act at once.
		if not is_momentary:
			self._nav_select_from_grid(x, y)
			return

		if is_press:
			# Pressing a different pad cancels a tap that is still waiting
			# for its possible second tap.
			pending = getattr(self, "_nav_pending_tap", None)
			if pending is not None and (pending[0], pending[1]) != (x, y):
				self._nav_pending_tap = None
			if not self._nav_held:
				self._nav_held = [(x, y)]
				self._nav_combo_done = False
				return
			if (x, y) in self._nav_held:
				return
			# A second button while the first one is held -> loop range
			anchor_x = self._nav_held[0][0]
			self._nav_held.append((x, y))
			self._nav_combo_done = True
			self._set_loop_from_nav_columns(anchor_x, x)
			return

		# --- release ---
		if (x, y) not in self._nav_held:
			return
		was_anchor = (self._nav_held[0] == (x, y))
		self._nav_held.remove((x, y))
		if self._nav_held:
			# Other button(s) still held: keep the gesture going
			return
		combo_done = self._nav_combo_done
		self._nav_combo_done = False
		if combo_done or not was_anchor:
			self._nav_pending_tap = None
			return

		now = time.time()
		pending = getattr(self, "_nav_pending_tap", None)
		if (pending is not None and (pending[0], pending[1]) == (x, y)
				and (now - pending[2]) < NAV_DOUBLE_TAP_WINDOW):
			# --- DOUBLE TAP: loop on this single block ---
			self._nav_pending_tap = None
			self._set_loop_from_nav_columns(x, x)
			return

		# --- FIRST TAP: wait to see if a second tap follows ---
		self._nav_pending_tap = (x, y, now)
		if hasattr(self._control_surface, 'schedule_message'):
			self._control_surface.schedule_message(
				NAV_DOUBLE_TAP_TICKS, self._execute_pending_nav_tap)
		else:
			self._execute_pending_nav_tap()

	def _execute_pending_nav_tap(self):
		"""Run a single tap that was not followed by a second one."""
		pending = getattr(self, "_nav_pending_tap", None)
		if pending is None:
			return
		if self._mode != STEPSEQ_MODE_NAV_OVERVIEW:
			self._nav_pending_tap = None
			return
		# Scheduler ticks are approximate: if called too early, try again.
		if (time.time() - pending[2]) < NAV_DOUBLE_TAP_WINDOW and \
				hasattr(self._control_surface, 'schedule_message'):
			self._control_surface.schedule_message(1, self._execute_pending_nav_tap)
			return
		self._nav_pending_tap = None
		# A new gesture is in progress on the same pad (2nd press held):
		# let its release decide.
		if self._nav_held:
			return
		self._nav_select_from_grid(pending[0], pending[1])

	def _nav_select_from_grid(self, x, y):
		"""Single tap in the navigation view: jump to block + band, back to notes."""
		page_offset = getattr(self._step_sequencer, '_loop_page_offset', 0)
		block_index = x + page_offset * 8
		target_band = self._overview_start_octave + (7 - y)
		target_band = max(0, min(self._nav_band_count() - 1, target_band))

		# A double tap on the navigation button comes back here
		self._previous_position = self._nav_entry_position
		# Also jump to the block this column represents
		self._select_block(block_index)
		if self._is_drum_view:
			# Drumrack: _display_octave holds the pitch block index
			self.set_display_octave(target_band)
			self._control_surface.show_message(
				"%s, Block %d" % (self._nav_band_label(target_band), block_index))
		elif self._is_focus_view:
			# Focus mode: the focus pitch moves by octaves to the tapped
			# band, the scale view's octave moves with it
			shift = target_band - self._focus_pitch // 12
			if self._focus_pitch + 12 * shift > 127:
				shift -= 1
			self._focus_pitch += 12 * shift
			self.set_display_octave(max(-1, min(MELODIC_OCTAVE_COUNT - 2,
			                                    self._display_octave + shift)))
			self._control_surface.show_message(
				"Focus %s, Block %d" % (self._pitch_name(self._focus_pitch), block_index))
		else:
			# Melodic: display octave = band - 1 (display octave 2 -> MIDI octave 3)
			selected_octave = target_band - 1
			self.set_display_octave(selected_octave)
			self._control_surface.show_message(
				"Octave %d, Block %d" % (selected_octave - 1, block_index))
		self.set_mode(STEPSEQ_MODE_NOTES)
		self._force_update = True
		self.update()

	def _land_moved_notes(self, x, y):
		"""
		Navigation view, moving notes: the tapped column is the block and
		the tapped row the octave (pitch block in drumrack) where the notes
		land. The anchor note lands in that block at the same place inside
		the block; every note keeps its place inside the octave / pitch
		block and its other parameters, and the notes keep their spacing.
		A note already where a moved note lands is replaced. The move is
		refused if a note would land outside the clip's start/end markers
		or outside the MIDI range. Then the notes view shows where they
		landed.
		Tapping the centre of the cross (where the notes come from) cancels
		the move: back to the notes view as it was.
		"""
		move = self._nav_move
		current_notes = [tuple(n) for n in self._note_cache]
		if any(n not in current_notes for n in move['notes']):
			# Changed in the meantime (e.g. edited in Live)
			self._control_surface.show_message("Move cancelled: the notes have changed")
			self.set_mode(STEPSEQ_MODE_NOTES)
			self.update()
			return
		page_offset = getattr(self._step_sequencer, '_loop_page_offset', 0)
		dest_block = x + page_offset * 8
		dest_band = self._overview_start_octave + (7 - y)
		block_len = 8 * self.resolution_beats
		block_shift = dest_block - int(move['anchor_time'] // block_len)
		band_shift = dest_band - self._displayed_nav_band(move['display_octave'], move['focus_pitch'])
		if block_shift == 0 and band_shift == 0:
			self._nav_move = None
			self._go_to_position(self._nav_entry_position)
			self.set_mode(STEPSEQ_MODE_NOTES)
			self._control_surface.show_message("Move cancelled")
			self._force_update = True
			self.update()
			return
		time_shift = block_shift * block_len
		pitch_shift = band_shift * self._view_band_unit()
		moved = [(n[0] + pitch_shift, n[1] + time_shift) + n[2:] for n in move['notes']]
		try:
			clip_start = float(self._clip.start_marker)
		except Exception:
			clip_start = 0.0
		clip_end = self._clip_total_end()
		if any(not (0 <= n[0] <= 127) for n in moved):
			self._control_surface.show_message("Move refused: notes outside the MIDI range")
			return
		if any(not (clip_start <= n[1] < clip_end) for n in moved):
			self._control_surface.show_message("Move refused: notes outside the clip")
			return

		self._nav_move = None
		kept = [n for n in current_notes if n not in move['notes']]
		# A note already where a moved note lands is replaced
		kept = [n for n in kept if not any(
			n[0] == m[0] and abs(n[1] - m[1]) < 0.0001 for m in moved)]
		new_notes = kept + moved
		self._write_note_cache_to_clip(new_notes)
		self._note_cache = tuple(new_notes)
		self._parse_notes()
		# A double tap on the navigation button comes back here
		self._previous_position = self._nav_entry_position
		display_octave = move['display_octave'] + band_shift
		if move['focus_pitch'] is not None:
			# Focus mode follows the notes
			self._focus_pitch = max(0, min(127, move['focus_pitch'] + pitch_shift))
			display_octave = max(-1, min(MELODIC_OCTAVE_COUNT - 2, display_octave))
		self._select_block(dest_block)
		self.set_display_octave(display_octave)
		self._control_surface.show_message("%d note(s) moved to block %d, %s" % (
			len(moved), dest_block, self._nav_band_label(dest_band)))
		self.set_mode(STEPSEQ_MODE_NOTES)
		self._force_update = True
		self.update()

	def _set_loop_from_nav_columns(self, x_first, x_second):
		"""
		Set the clip loop from two navigation-view columns (blocks of 8 steps).
		Loop = first step of min(block) .. last step of max(block), inclusive.
		"""
		ss = self._step_sequencer
		ls = getattr(ss, '_loop_selector', None)
		if ls is None or self._clip is None:
			return
		if getattr(ls, '_clip', None) is None:
			ls.set_clip(self._clip)
		page_offset = getattr(ss, '_loop_page_offset', 0)
		block_a = x_first + page_offset * 8
		block_b = x_second + page_offset * 8
		first_block = min(block_a, block_b)
		last_block = max(block_a, block_b)

		restored = ls.set_loop_from_blocks(first_block, last_block)
		if restored is None:
			return

		# Keep the three loop-block mirrors in sync (absolute index)
		ss._loop_block = first_block
		self._loop_block = first_block
		self._page = first_block

		if restored:
			self._control_surface.show_message("Loop: reset to clip markers")
		else:
			steps_per_block = 8
			self._control_surface.show_message(
				"Loop: blocks %d-%d (steps %d-%d)" % (
					first_block, last_block,
					first_block * steps_per_block,
					(last_block + 1) * steps_per_block - 1))
		ss._update_cycle_button()
		self._force_update = True
		self.update()
		if not getattr(self, "_loading_clip", False):
			self.sync_clip_with_json()

	def _clip_total_end(self):
		"""
		End (in beats) of the clip's total length, independent of the loop.

		Live API note: Clip.length is the LOOP length for looped clips, so
		it cannot be used here. Clip.end_marker is the loop-independent
		end of the clip ("independent of the loop state" in the LOM).
		Loop changes made from the Launchpad never pull the end marker in.
		"""
		if self._clip is None:
			return 0.0
		try:
			return float(self._clip.end_marker)
		except Exception:
			try:
				return float(self._clip.loop_end)
			except Exception:
				return 0.0

	def _get_notes_in_block(self, block_index):
		"""All unmuted notes whose onset falls inside block_index (8 steps)."""
		block_start = block_index * 8 * self.resolution_beats
		block_end = block_start + 8 * self.resolution_beats
		return [n for n in self._note_cache
		        if block_start <= n[1] < block_end and not n[4]]

	def _select_block(self, block_index):
		"""
        Jump the sequencer to an absolute block: syncs page offset,
        LoopSelector._block (ABSOLUTE), parent + editor mirrors, and _page.
        Does NOT touch the clip loop — selection is editing position only.
        """
		ss = self._step_sequencer
		ls = getattr(ss, '_loop_selector', None)
		if ls is None or self._clip is None:
			return

		# Clamp to the clip's block extent. Use the END of the clip content
		# (not the loop length) so blocks after a loop that does not start
		# at 0 - e.g. one set from the navigation view - stay selectable.
		clip_end = self._clip_total_end()
		total_steps = int(clip_end / ss._resolution)
		total_blocks = max(1, (total_steps + 7) // 8)
		block_index = max(0, min(block_index, total_blocks - 1))
		new_offset = block_index // 8

		# 1. Offset everywhere (cycle button reads ss._loop_page_offset)
		ss._loop_page_offset = new_offset
		ls._loop_page_offset = new_offset
		if hasattr(ls, '_last_known_offset'):
			ls._last_known_offset = new_offset - 1  # force cache clear in update()

		# 2. Three-way block sync — note _block is ABSOLUTE here
		ss._loop_block = block_index
		ls._block = block_index
		ls._force = True
		self._loop_block = block_index

		# 3. Melodic editor page = absolute block (metronome/copy-paste correctness)
		self._page = block_index
		ss.set_page(block_index)
		ss._update_cycle_button()
		self._force_update = True
		self.update()
		if not getattr(self, "_loading_clip", False):
			self.sync_clip_with_json()

# VELOCITIES
	def _on_velocity_cc_value(self, value):
		# Velocity 0 would make a silent note: keep it at least 1
		self._note_velocity = max(1, min(127, value))

	def _set_velocity_at_step(self, idx, velocity_index):
		velocity = self._velocity_map[velocity_index]
		notes = list(self._note_cache)
		start_time = idx * self.resolution_beats
		end_time = start_time + self.resolution_beats
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
					self._velocity_wait_animation = False
					self._pending_velocity_editor = False
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
					# The animation uses row 7: take it from the loop selector
					self._step_sequencer._update_loop_selector()

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
				* self.resolution_beats
				/ 4.0
		)
		notes = list(self._note_cache)
		start_time = idx * self.resolution_beats
		end_time = start_time + self.resolution_beats
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
					# The animation uses row 7: take it from the loop selector
					self._step_sequencer._update_loop_selector()
					# Schedule the animation loop
					self._control_surface.schedule_message(1, self._length_wait_tick)
					self._control_surface.show_message("Select step or press Length button again")

					# Update LED state based on animation status (The code you found)
					if self._length_wait_animation:
						self._mode_notes_lengths_button.turn_on()
					else:
						self._mode_notes_lengths_button.turn_off()

			# Handle Button Hold (Non-Momentary) -> Shift Logic
			else:
				self._is_notes_lengths_shifted = True
				self._is_mute_shifted = True
				self._step_sequencer._is_mute_shifted = self._is_mute_shifted
			self.update()
			self._step_sequencer._update_OSD()
		else:
			# Clip not selected, turn off button
			if self._mode_notes_lengths_button:
				self._mode_notes_lengths_button.set_light("DefaultButton.Disabled")



	# COPY / PASTE / DELETE
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

	def _update_copy_paste_button_state(self):
		if not hasattr(self, '_mode_copy_paste_button') or not self._mode_copy_paste_button:
			return

		if self._paste_armed and self._copied_grid_data:
			self._mode_copy_paste_button.set_light("StepSequencer2.CopyPaste.Copied")
		else:
			self._mode_copy_paste_button.set_light("StepSequencer2.CopyPaste.Dim")

	def _mode_button_copy_paste_value(self, value, sender):
		assert (self._mode_copy_paste_button != None)
		assert (value in range(128))
		# Toggle held + Copy taps act on the whole clip, also on an empty slot
		if self.is_enabled() and self._handle_clip_gesture(value):
			return
		if self.is_enabled() and self._clip != None:
			current_mode = self._mode
			now = time.time()

			# --- PROHIBIT DURING ANIMATIONS ---
			# If velocity or length animation is running, ignore copy/paste buttons
			if self._velocity_wait_animation or self._length_wait_animation:
				return

			# --- MODE CHECK ---
			# If not in Notes mode, we do nothing EXCEPT potentially updating the LED.
			# We DO NOT clear the buffer here. The buffer persists for the next session in Notes mode.
			if current_mode != STEPSEQ_MODE_NOTES:
				# Update LED to show Dim (not armed) if not in Notes mode?
				# Or keep it On if data exists? Let's keep it On to remind user they have data.
				if self._paste_armed and self._copied_grid_data:
					pass
				else:
					self._update_copy_paste_button_state()

				# Do NOT clear _copied_grid_data or _paste_armed here!
				# Just exit.
				return

			# --- Skip if triggered via Toggle Sequence (Scenario B) ---
			if self._skip_copy_release:
				self._skip_copy_release = False
				return

			# --- PRESS EVENT (value > 0) ---
			if value > 0:
				# (Toggle already held is handled by _handle_clip_gesture)
				# MULTI-TAP DETECTION LOGIC (2nd press = erase, 3rd press = cut)
				if (now - self._last_press_time) < 0.25:
					self._copy_tap_count += 1
				else:
					self._copy_tap_count = 1
					self._erase_just_done = False
				self._delete_action_on_release = (self._copy_tap_count == 2)
				self._cut_action_on_release = (self._copy_tap_count == 3)
				self._last_press_time = now
				return

			# --- RELEASE EVENT (value == 0) ---

			# 2. Handle Delete (Double Click Detected on Press)
			if self._delete_action_on_release:
				self._delete_action_on_release = False
				if current_mode == STEPSEQ_MODE_NOTES:
					# Empty grid: double-tap restores the last erased notes (undo)
					if not self._notes_in_current_view() and self._erased_grid_data:
						restored = self._paste_notes_from_buffer(self._erased_grid_data)
						if restored:
							self._erased_grid_data = None
							self._control_surface.show_message("Undo erase (%d notes)" % restored)
						return
					# Clear buffer ONLY on successful delete (optional, but good practice to clear after delete)
					# Or keep it? Usually delete clears the view, so buffer might be stale?
					# Let's clear buffer on delete to avoid pasting deleted notes.
					self._paste_armed = False
					self._copied_grid_data = None
					self._update_copy_paste_button_state()
					self._control_surface.show_message("Erase Page")
					self._erase_just_done = self._delete_notes_in_current_page() > 0
					self.update()
				return

			# 2b. Handle Cut (Triple Click): the erase done by the double click
			# becomes a cut — the erased notes go to the paste buffer instead.
			if self._cut_action_on_release:
				self._cut_action_on_release = False
				if current_mode == STEPSEQ_MODE_NOTES and self._erase_just_done and self._erased_grid_data:
					self._erase_just_done = False
					self._copied_grid_data = self._erased_grid_data
					self._erased_grid_data = None
					self._paste_armed = True
					self._control_surface.show_message("Cut (%d notes)" % len(self._copied_grid_data))
					self._update_copy_paste_button_state()
				return

			# Further taps of a fast sequence (4th and beyond) do nothing
			if self._copy_tap_count > 3:
				return

			# 3. Safety Check: Is Toggle held at release? (Redundant but safe)
			duplicate_trigger = False
			if hasattr(self, '_clip_toggle_button') and self._clip_toggle_button:
				if self._clip_toggle_button.is_pressed():
					duplicate_trigger = True
			if duplicate_trigger:
				if current_mode == STEPSEQ_MODE_NOTES:
					self._duplicate_clip_to_next_slot()
					if hasattr(self, '_step_sequencer') and self._step_sequencer:
						if not hasattr(self._step_sequencer, '_suppress_toggle_release'):
							self._step_sequencer._suppress_toggle_release = False
						self._step_sequencer._suppress_toggle_release = True
				return

			# 4. Normal Copy / Paste Logic
			if current_mode == STEPSEQ_MODE_NOTES:
				if self._paste_armed:
					if self._copied_grid_data:
						self._paste_notes_from_buffer()
						self._control_surface.show_message("Pasted (%d notes)" % len(self._copied_grid_data))

					# Clear buffer AFTER pasting
					self._copied_grid_data = None
					self._paste_armed = False
					self._update_copy_paste_button_state()
				else:
					self._copy_notes_to_buffer()
					if self._copied_grid_data:
						self._paste_armed = True
						self._control_surface.show_message("Armed (%d notes)" % len(self._copied_grid_data))
						self._update_copy_paste_button_state()
					else:
						self._control_surface.show_message("Nothing to copy")

		else:
			if self._mode_copy_paste_button:
				self._mode_copy_paste_button.set_light("DefaultButton.Disabled")

	def _copy_notes_to_buffer(self):
		"""Copies ALL notes in current page AND current octave / pitch block from note_cache."""
		self._copied_grid_data = self._notes_in_current_view()

	def _notes_in_current_view(self):
		"""Returns the notes in current page AND current octave / pitch block,
		as (pitch, time relative to page start, length, velocity, muted)."""
		self._parse_notes()
		page_start_step = self._page * 8
		page_end_step = (self._page + 1) * 8

		# CRITICAL: Use SAFE accessor that guarantees beat value ≥ 0.001
		resolution = self.resolution_beats  # ← CHANGED FROM self._resolution

		buffer = []
		for n in self._note_cache:
			note_pitch = n[0]
			note_time = n[1]
			note_length = n[2]
			note_velocity = n[3]
			note_muted = n[4]

			# Check 1: Is note in current page time range?
			if not (page_start_step * resolution <= note_time < page_end_step * resolution):
				continue

			# Check 2: Does note belong to current displayed octave / block (focus: rows)?
			if not self._is_pitch_in_current_view(note_pitch):
				continue
			relative_time = note_time - (page_start_step * resolution)
			buffer.append((note_pitch, relative_time, note_length, note_velocity, note_muted))
		return buffer

	def _paste_notes_from_buffer(self, buffer=None):
		"""
		Pastes notes from buffer (default: the copy buffer) onto the CURRENT page and CURRENT
		displayed octave / pitch block. It preserves the relative pattern (step offset) but
		shifts pitch and time to match current view. Returns the number of notes pasted.
		"""
		if buffer is None:
			buffer = self._copied_grid_data
		if not buffer:
			return 0
		page_start_step = self._page * 8
		current_page_offset_time = page_start_step * self.resolution_beats

		# 1. Determine the Target Pitch Range
		# Band (MIDI octave / pitch block) of the CURRENT display, and its size in semitones.
		target_band = self._current_view_band()
		band_unit = self._view_band_unit()
		# Focus mode: the whole pattern moves by the octaves that bring its
		# middle closest to the focus pitch
		focus_shift = None
		if self._is_focus_view:
			pitches = [n[0] for n in buffer]
			middle = (min(pitches) + max(pitches)) / 2.0
			focus_shift = 12 * int(round((self._focus_pitch - middle) / 12.0))
		new_notes = []
		added_count = 0
		for n in buffer:
			orig_pitch = n[0]
			rel_time = n[1]
			orig_length = n[2]
			orig_velocity = n[3]
			orig_muted = n[4]

			# Calculate original band of the note
			orig_band = self._nav_band_of_pitch(orig_pitch)

			# --- PITCH SHIFTING LOGIC ---
			if focus_shift is not None:
				new_pitch = orig_pitch + focus_shift
			else:
				# Shift by whole bands (12 semitones melodic, 8 semitones drumrack)
				band_shift = target_band - orig_band
				new_pitch = orig_pitch + (band_shift * band_unit)

			# Ensure pitch stays within valid MIDI range (0-127)
			if new_pitch < 0 or new_pitch > 127:
				continue  # Skip notes that would be out of bounds

			# --- TIME PLACEMENT ---
			# Place the note at the SAME relative step on the NEW page
			new_time = current_page_offset_time + rel_time

			# Add the transposed note
			new_notes.append((new_pitch, new_time, orig_length, orig_velocity, orig_muted))
			added_count += 1
		if added_count == 0:
			self._control_surface.show_message("Pattern Out of Range")
			return 0

		# --- MERGE WITH EXISTING NOTES ---
		all_clip_notes = list(self._note_cache)
		final_notes = []

		# Filter out duplicates (notes at same pitch/time)
		for n in all_clip_notes:
			is_duplicate = False
			for p_note in new_notes:
				if abs(n[1] - p_note[1]) < 0.0001 and n[0] == p_note[0]:
					is_duplicate = True
					break
			if not is_duplicate:
				final_notes.append(n)

		# Add our new transposed notes
		final_notes.extend(new_notes)

		# Write to clip
		self._clip.select_all_notes()
		self._clip.replace_selected_notes(tuple(final_notes))
		self._note_cache = tuple(final_notes)
		self._parse_notes()
		self.update()
		self._control_surface.show_message("Pasted (%d notes)" % added_count)
		return added_count

	def _delete_notes_in_current_page(self):
		"""Deletes ALL notes in current page AND current octave / pitch block (in-scale + out-of-scale).
		The erased notes are kept so a double-tap on the empty grid can restore them.
		Returns the number of notes erased."""
		erased = self._notes_in_current_view()
		page_start_step = self._page * 8
		page_end_step = (self._page + 1) * 8
		resolution = self.resolution_beats

		cleaned_notes = []
		deleted_count = 0
		for n in self._note_cache:
			note_pitch = n[0]
			note_time = n[1]
			is_deleted = False

			# Check 1: Is note in current page time range?
			if not (page_start_step * resolution <= note_time < page_end_step * resolution):
				# Not on this page -> KEEP IT
				cleaned_notes.append(n)
				continue

			# Check 2: Does note belong to current displayed octave / block (focus: rows)?
			if self._is_pitch_in_current_view(note_pitch):
				# It matches current page AND current band -> DELETE IT
				is_deleted = True
				deleted_count += 1
			else:
				# Different band -> KEEP IT
				cleaned_notes.append(n)
		if deleted_count > 0:
			self._clip.select_all_notes()
			self._clip.replace_selected_notes(tuple(cleaned_notes))
			self._note_cache = tuple(cleaned_notes)
			self._parse_notes()
			self.update()
			self._erased_grid_data = erased
			self._control_surface.show_message("%d erased" % deleted_count)
		else:
			self._control_surface.show_message("Nothing to erase")
		return deleted_count

	# CLIP GESTURES (Toggle held + Copy taps)
	def _handle_clip_gesture(self, value):
		"""
		Toggle held + Copy taps: 1 tap duplicates the clip, 2 taps delete it
		(or restore the last deleted clip when the slot is empty).
		Returns True when the Copy event belongs to this gesture.
		"""
		if value > 0:
			if self._mode != STEPSEQ_MODE_NOTES:
				return False
			if self._velocity_wait_animation or self._length_wait_animation:
				return False
			if not (self._clip_toggle_button and self._clip_toggle_button.is_pressed()):
				return False
			now = time.time()
			if self._pending_duplicate_time is not None:
				# Second tap before the single-tap duplicate ran: double tap
				self._pending_duplicate_time = None
				self._clip_gesture_count = 2
			elif (self._clip_gesture_count >= 2 and
					(now - self._clip_gesture_last_release) < CLIP_GESTURE_TAP_WINDOW):
				# Extra taps right after a double tap: ignored
				self._clip_gesture_count += 1
			else:
				self._clip_gesture_count = 1
			if DEBUG_LOGGING:
				slot = self.song().view.highlighted_clip_slot
				self._control_surface.log_message(
					f"[CLIP_GESTURE] tap={self._clip_gesture_count} "
					f"slot_has_clip={slot is not None and slot.has_clip} "
					f"deleted_clip_saved={self._deleted_clip_data is not None}"
				)
			self._clip_gesture_active = True
			# Toggle must not start/stop the clip when it is released
			self._duplicate_pending = True
			if hasattr(self, '_step_sequencer') and self._step_sequencer:
				self._step_sequencer._suppress_toggle_release = True
			return True

		if not self._clip_gesture_active:
			return False
		self._clip_gesture_active = False
		self._duplicate_pending = False
		self._clip_gesture_last_release = time.time()
		if self._clip_gesture_count == 1:
			# Wait to see whether a second tap follows before duplicating
			self._pending_duplicate_time = self._clip_gesture_last_release
			self._control_surface.schedule_message(
				CLIP_GESTURE_TAP_TICKS, self._execute_pending_duplicate)
		elif self._clip_gesture_count == 2:
			slot = self.song().view.highlighted_clip_slot
			if slot is not None and slot.has_clip:
				self._delete_highlighted_clip()
			else:
				self._restore_deleted_clip()
		return True

	def _execute_pending_duplicate(self):
		"""Run a Toggle + single Copy tap that was not followed by a second tap."""
		pending = self._pending_duplicate_time
		if pending is None:
			return
		# Scheduler ticks are approximate: if called too early, try again.
		if (time.time() - pending) < CLIP_GESTURE_TAP_WINDOW:
			self._control_surface.schedule_message(1, self._execute_pending_duplicate)
			return
		self._pending_duplicate_time = None
		if not (self.is_enabled() and self._mode == STEPSEQ_MODE_NOTES):
			return
		slot = self.song().view.highlighted_clip_slot
		if (slot is None or not slot.has_clip) and self._deleted_clip_data:
			# Nothing to duplicate here; tell how to get the deleted clip back
			self._control_surface.show_message("Double-tap to restore the deleted clip")
			return
		self._duplicate_clip_to_next_slot()

	_CLIP_SNAPSHOT_ATTRS = (
		'color_index', 'signature_numerator', 'signature_denominator',
		'launch_mode', 'launch_quantization', 'legato', 'velocity_amount', 'muted',
	)

	def _snapshot_clip(self, clip):
		"""Captures everything needed to rebuild a MIDI clip: notes, loop, markers, name (SUX tag)."""
		clip.select_all_notes()
		raw_notes = clip.get_selected_notes_extended()
		clip.deselect_all_notes()
		notes = []
		for n in raw_notes or ():
			notes.append({
				'pitch': int(n.pitch),
				'start_time': float(n.start_time),
				'duration': float(n.duration),
				'velocity': float(n.velocity),
				'mute': bool(n.mute),
				'probability': float(getattr(n, 'probability', 1.0)),
				'velocity_deviation': float(getattr(n, 'velocity_deviation', 0.0)),
				'release_velocity': float(getattr(n, 'release_velocity', 64.0)),
			})
		data = {
			'name': clip.name,
			'looping': bool(clip.looping),
			'loop_start': float(clip.loop_start),
			'loop_end': float(clip.loop_end),
			'start_marker': float(clip.start_marker),
			'end_marker': float(clip.end_marker),
			'notes': notes,
			'attrs': {},
		}
		for attr in self._CLIP_SNAPSHOT_ATTRS:
			try:
				data['attrs'][attr] = getattr(clip, attr)
			except Exception:
				pass
		return data

	def _delete_highlighted_clip(self):
		"""Deletes the clip in view, keeping a snapshot so it can be restored."""
		slot = self.song().view.highlighted_clip_slot
		if slot is None or not slot.has_clip:
			self._control_surface.show_message("No clip to delete")
			return
		clip = slot.clip
		if not clip.is_midi_clip:
			self._control_surface.show_message("Not a MIDI clip")
			return
		try:
			snapshot = self._snapshot_clip(clip)
		except Exception as e:
			self._control_surface.log_message(f"[CLIP_DELETE] Snapshot failed: {e}")
			self._control_surface.show_message("Clip delete failed")
			return
		self._deleted_clip_data = snapshot
		slot.delete_clip()
		self._control_surface.show_message("Clip deleted (%d notes)" % len(snapshot['notes']))

	def _restore_deleted_clip(self):
		"""Rebuilds the last deleted clip in the (empty) slot in view."""
		data = self._deleted_clip_data
		if not data:
			self._control_surface.show_message("No deleted clip to restore")
			return
		slot = self.song().view.highlighted_clip_slot
		if slot is None or slot.has_clip:
			return

		# Build the clip completely before the step sequencer sees it, so it
		# loads the restored SUX tag instead of defaults for a new clip.
		parent = getattr(self, '_step_sequencer', None)
		listener = getattr(parent, 'on_clip_slot_has_clip_changed', None)
		had_listener = False
		if listener is not None:
			try:
				had_listener = slot.has_clip_has_listener(listener)
				if had_listener:
					slot.remove_has_clip_listener(listener)
			except Exception:
				had_listener = False
		restored = False
		try:
			extent = max(data['loop_end'], data['end_marker'])
			slot.create_clip(extent)
			self._apply_clip_snapshot(slot.clip, data)
			restored = True
		except Exception as e:
			self._control_surface.log_message(f"[CLIP_RESTORE] Failed: {e}")
		if had_listener:
			try:
				slot.add_has_clip_listener(listener)
			except Exception:
				pass
			# Let the step sequencer pick up the new clip (even a partial one)
			if slot.has_clip:
				listener()
		self._update_clip_toggle_button()
		if restored:
			self._deleted_clip_data = None
			self._control_surface.show_message("Clip restored (%d notes)" % len(data['notes']))
		else:
			self._control_surface.show_message("Clip restore failed")

	def _apply_clip_snapshot(self, clip, data):
		"""Writes a snapshot from _snapshot_clip onto a freshly created clip."""
		# Loop and markers (looping on while editing, so both can be set)
		clip.looping = True
		apply_clip_loop(clip, data['loop_start'], data['loop_end'])
		try:
			if data['end_marker'] > clip.end_marker:
				clip.end_marker = data['end_marker']
			clip.start_marker = data['start_marker']
			clip.end_marker = data['end_marker']
		except Exception as e:
			self._control_surface.log_message(f"[CLIP_RESTORE] Markers: {e}")
		clip.looping = data['looping']

		for attr, value in data['attrs'].items():
			try:
				setattr(clip, attr, value)
			except Exception:
				pass

		notes = data['notes']
		if notes:
			try:
				from Live.Clip import MidiNoteSpecification
				clip.add_new_notes(tuple(MidiNoteSpecification(**n) for n in notes))
			except Exception:
				# Older API: basic note tuples
				clip.select_all_notes()
				clip.replace_selected_notes(tuple(
					(n['pitch'], n['start_time'], n['duration'], int(n['velocity']), n['mute'])
					for n in notes))
				clip.deselect_all_notes()

		# Name last: it carries the SUX tag with the step sequencer settings
		clip.name = data['name']

	def _duplicate_clip_to_next_slot(self):
		"""Duplicates the current clip to the next slot or a new scene."""
		song = self.song()
		current_track = song.view.selected_track
		if not current_track or len(current_track.clip_slots) == 0:
			self._control_surface.show_message("No valid track/slots")
			return
		highlighted_clip_slot = song.view.highlighted_clip_slot
		if not highlighted_clip_slot or not highlighted_clip_slot.has_clip:
			self._control_surface.show_message("No clip in highlighted slot")
			return
		slot_list = list(current_track.clip_slots)
		try:
			current_slot_index = slot_list.index(highlighted_clip_slot)
		except ValueError:
			self._control_surface.show_message("Slot not found")
			return
		next_slot_index = current_slot_index + 1

		# --- CHECK IF WE NEED A NEW SCENE ---
		if next_slot_index >= len(slot_list):
			# No more slots below -> Create a new Scene at the end
			new_scene_index = len(song.scenes)
			try:
				song.create_scene(new_scene_index)
				self._control_surface.show_message("New Scene Created")

				# Refresh the slot list for the current track
				# Creating a scene automatically adds a slot to every track
				slot_list = list(current_track.clip_slots)

				# The new slot is now the last one in the list
				next_slot_index = len(slot_list) - 1
				next_clip_slot = slot_list[next_slot_index]
				if not next_clip_slot:
					self._control_surface.show_message("Error: Slot not found")
					return
			except Exception as e:
				self._control_surface.log_message(f"[DUP] Scene creation failed: {e}")
				self._control_surface.show_message("Failed to create scene")
				return
		else:
			# Normal case: Next slot exists
			next_clip_slot = slot_list[next_slot_index]
		src_clip = highlighted_clip_slot.clip
		if not src_clip.is_midi_clip:
			self._control_surface.show_message("Not a MIDI clip")
			return
		try:
			snapshot = self._snapshot_clip(src_clip)
		except Exception as e:
			self._control_surface.log_message(f"[DUP] Snapshot failed: {e}")
			self._control_surface.show_message("Error reading source clip")
			return
		if next_clip_slot.has_clip and next_clip_slot.is_playing:
			next_clip_slot.stop()

		# --- COPY THE WHOLE CLIP (name with SUX tag, loop, markers, notes) ---
		# This must be complete BEFORE the new slot is highlighted: the step
		# sequencer then loads the copied SUX tag instead of writing defaults.
		try:
			# Live 11+: native copy, identical to Live's own duplicate
			highlighted_clip_slot.duplicate_clip_to(next_clip_slot)
		except Exception as e:
			self._control_surface.log_message(f"[DUP] duplicate_clip_to unavailable ({e}), rebuilding clip")
			try:
				if next_clip_slot.has_clip:
					next_clip_slot.delete_clip()
				next_clip_slot.create_clip(max(snapshot['loop_end'], snapshot['end_marker']))
				self._apply_clip_snapshot(next_clip_slot.clip, snapshot)
			except Exception as e2:
				self._control_surface.log_message(f"[DUP] Application Error: {e2}")
				self._control_surface.show_message("Duplication Failed")
				return

		# Highlight the new clip slot so the user sees it immediately
		# This works for both existing slots and newly created scenes
		if hasattr(song, 'view'):
			song.view.highlighted_clip_slot = next_clip_slot
		self._control_surface.show_message("Duplicated! (%d notes)" % len(snapshot['notes']))

	# UTILS
	def uses_bottom_row(self):
		if getattr(self, '_is_drumrack_layout', False):
			return True
		# The velocity/length waiting animation covers row 7 too
		if (getattr(self, '_velocity_wait_animation', False) or
				getattr(self, '_length_wait_animation', False)):
			return True
		return self._mode in (
			STEPSEQ_MODE_STEP_VELOCITY_EDITOR,
			STEPSEQ_MODE_STEP_LENGTH_EDITOR,
			STEPSEQ_MODE_VERTICAL_VELOCITY,
			STEPSEQ_MODE_VERTICAL_LENGTH,
			STEPSEQ_MODE_COPY_PASTE,
			STEPSEQ_MODE_NAV_OVERVIEW,
		)

class StepSequencerComponent2(StepSequencerComponent):
	def __init__(self, matrix, side_buttons, top_buttons, control_surface):
		self._is_drumrack_mode = False
		self._new_clip_pages = 1
		self._name = "melodic step sequencer"
		super(StepSequencerComponent2, self).__init__(matrix, side_buttons, top_buttons, control_surface)
		self._loop_block = 0
		# Resolution button taps (see _resolution_button_value)
		self._last_res_button_press = time.time()
		self._res_pending_tap_time = None  # release time of a tap waiting for a 2nd one
		self._res_double_tap = False  # the press in progress is a double tap's 2nd tap
		self._res_anchor_time = None  # held note (beats) to zoom around, or None

	def _loop_selector_should_be_enabled(self):
		if hasattr(self, '_scale_selector') and self._scale_selector:
			if self._scale_selector.is_drumrack:
				return False
		if getattr(self, '_is_drumrack_mode', False):
			return False
		# The velocity/length waiting animation uses row 7 too
		note_editor = getattr(self, '_note_editor', None)
		if (getattr(note_editor, '_velocity_wait_animation', False) or
				getattr(note_editor, '_length_wait_animation', False)):
			return False
		return super(StepSequencerComponent2, self)._loop_selector_should_be_enabled()

	def _search_and_relink_clip(self, target_hash, cached_entry):
		"""Find clip that matched this hash anywhere in the song."""
		try:
			song = self.song()
			for track_idx, track in enumerate(list(song.tracks)):
				for slot_idx, slot in enumerate(list(track.clip_slots)):
					if slot.has_clip:
						clip = slot.clip
						current_hash = self._meta_manager._hash_clip_content(clip)
						if current_hash == target_hash:
							cached_entry["track_index"] = track_idx
							cached_entry["slot_index"] = slot_idx
							cached_entry["moved_count"] = cached_entry.get("moved_count", 0) + 1
							cached_entry["updated_at"] = time.time()
							self._meta_manager._save_cache()
							return True
			return False
		except Exception as e:
			self._control_surface.log_message(f"[SEARCH_ERROR] {e}")
			return False

	def report_move_anomaly(self, clip, expected_hash, actual_hash):
		"""Log suspicious clip movements that might indicate user tampering."""
		import hashlib

		# Alert if hash changed significantly (>20% difference)
		similarity = sum(a == b for a, b in zip(expected_hash, actual_hash)) / max(len(expected_hash), len(actual_hash))
		if similarity < 0.8:
			# Can optionally send Live message notification
			self.surface.show_message("Clip parameters may be out of sync - check settings manually")

	def _set_scale_selector(self):
		super(StepSequencerComponent2, self)._set_scale_selector()
		self._scale_selector._mode = "diatonic"
		self._scale_selector._is_drumrack = False
		self._scale_selector._drumrack_changed_callback = (
			self._on_drumrack_changed
		)

	def _on_drumrack_changed(self, is_drumrack):
		"""
        Called by ScaleComponent when the USER changes Drum Rack mode.
        This is deliberately separate from clip loading:
          - loading a clip directly assigns selector._drumrack
          - user interaction calls set_drumrack(), which reaches here
        """
		is_drumrack = bool(is_drumrack)
		# Keep the StepSequencer/UI layout synchronized immediately.
		self._sync_editor_layout()

		# Never save while a clip is being loaded.
		if getattr(self._note_editor, '_loading_clip', False):
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					"[DRUMRACK_CHANGED] Suppressed sync during clip load"
				)
			return

		# Live's Scale toggle follows the user's choice: off for drumrack,
		# back on when the clip returns to melodic mode.
		self.request_live_scale_mode(not is_drumrack)

		# Persist the user's change to the currently selected clip.
		if (hasattr(self, '_note_editor') and
				self._note_editor and
				getattr(self._note_editor, '_clip', None) and
				getattr(self._note_editor, '_meta_manager', None)):
			try:
				self._note_editor.sync_clip_with_json()
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[DRUMRACK_CHANGED] Clip metadata synced "
						f"is_drumrack={is_drumrack}"
					)
			except Exception as e:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[DRUMRACK_CHANGED_SYNC_ERROR] {e}"
					)

	# --- LIVE SCALE TOGGLE (Live 12+: Song.scale_mode) ---
	def request_live_scale_mode(self, enabled):
		"""
		Turn Live's "Scale" toggle on/off. In Live 12, Song.scale_mode mirrors
		the Scale toggle of the clip shown in Live, so this applies to the
		clip being edited. Deferred one tick because Live refuses changes
		made from inside a notification. No-op on Live versions without it.
		"""
		self._pending_scale_mode = bool(enabled)
		try:
			self._control_surface.schedule_message(
				SCALE_MODE_DELAY_TICKS, self._apply_live_scale_mode)
		except Exception:
			self._apply_live_scale_mode()

	def _apply_live_scale_mode(self):
		enabled = getattr(self, '_pending_scale_mode', None)
		self._pending_scale_mode = None
		if enabled is None:
			return
		try:
			song = self.song()
			if not hasattr(song, 'scale_mode'):
				return  # Live < 12
			if bool(song.scale_mode) != enabled:
				song.scale_mode = enabled
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[SCALE_MODE] Live scale toggle -> {enabled}")
		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[SCALE_MODE_ERROR] {e}")

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

	def _update_buttons(self):
		self._update_resolution_button()
		self._update_clip_toggle_button()
		self._update_scale_selector_button()
		self._update_left_button()
		self._update_right_button()

	# --- SCALE BUTTON: SCALE EDITOR / FOCUS MODE ---
	def _scale_selector_button_value(self, value):
		"""
		Melodic notes view: a note pad held + tap = focus mode around that
		note, tap in focus mode = back to the normal notes view (see
		MelodicNoteEditorComponent.handle_focus_button). Otherwise the
		scale editor, as before.
		"""
		if (self.is_enabled() and value > 0 and self._mode != STEPSEQ_MODE_SCALE_EDIT
				and self._note_editor.handle_focus_button()):
			return
		super(StepSequencerComponent2, self)._scale_selector_button_value(value)

	def _update_scale_selector_button(self):
		super(StepSequencerComponent2, self)._update_scale_selector_button()
		# Lit in focus mode too: a tap leaves it
		if (self.is_enabled() and self._scale_selector_button is not None
				and self._clip is not None and self._mode != STEPSEQ_MODE_SCALE_EDIT
				and self._note_editor._is_focus_view):
			self._scale_selector_button.turn_on()

	# --- RESOLUTION (ZOOM) ---
	def _resolution_button_value(self, value, sender):
		"""
		- Tap: zoom in (finer resolution; from the finest one it wraps
		  around to the coarsest).
		- Double tap: zoom out (coarser resolution; from the coarsest one
		  it wraps around to the finest).
		- Long press: middle resolution.
		The view stays on the same part of the clip (see _change_resolution).
		While a pad of the notes view is held, the zoom goes around that
		note instead of around the first step shown.

		Because of the double tap, a single tap zooms in only once the
		double-tap window has elapsed without a second tap.
		"""
		assert (self._resolution_button != None)
		assert (value in range(128))
		if not self.is_enabled() or self._clip is None:
			return
		now = time.time()
		if (value != 0) or (not sender.is_momentary()):
			self._last_res_button_press = now
			self._res_double_tap = False
			anchor = self._note_editor.take_zoom_anchor()
			pending = self._res_pending_tap_time
			self._res_pending_tap_time = None
			if pending is not None:
				if (now - pending) < RESOLUTION_DOUBLE_TAP_WINDOW:
					# Second tap before the single-tap zoom in ran: double tap
					self._res_double_tap = True
					if anchor is None:
						# Note released between the taps: keep the first tap's
						anchor = self._res_anchor_time
				else:
					# Window over but the scheduler has not run the tap yet
					self._zoom_in()
			self._res_anchor_time = anchor
			return

		# --- release ---
		if (now - self._last_res_button_press) > RESOLUTION_LONG_PRESS:
			self._res_double_tap = False
			self._change_resolution(len(RESOLUTION_MAP) // 2, self._res_anchor_time)
		elif self._res_double_tap:
			self._res_double_tap = False
			self._change_resolution(
				(self._resolution_index + 1) % len(RESOLUTION_MAP), self._res_anchor_time)
		else:
			# First tap: wait to see if a second tap follows
			self._res_pending_tap_time = now
			self._control_surface.schedule_message(
				RESOLUTION_DOUBLE_TAP_TICKS, self._execute_pending_resolution_tap)

	def _execute_pending_resolution_tap(self):
		"""Run a single tap on the resolution button that was not followed by a second one."""
		pending = self._res_pending_tap_time
		if pending is None:
			return
		# Scheduler ticks are approximate: if called too early, try again.
		if (time.time() - pending) < RESOLUTION_DOUBLE_TAP_WINDOW:
			self._control_surface.schedule_message(1, self._execute_pending_resolution_tap)
			return
		self._res_pending_tap_time = None
		if not self.is_enabled() or self._clip is None:
			return
		self._zoom_in()

	def _zoom_in(self):
		self._change_resolution(
			(self._resolution_index - 1) % len(RESOLUTION_MAP), self._res_anchor_time)

	def _change_resolution(self, new_index, anchor_time=None):
		"""
		Switch to RESOLUTION_MAP[new_index], keeping the view on the same
		part of the clip: the new selected block is the one that holds the
		anchor, i.e. the held note (anchor_time, in beats) or else the
		first step shown before. Zooming in by one level shows the half of
		the old block that holds the anchor (block * 2, or block * 2 + 1
		for a held note in the second half), zooming out the block that
		contains the old one (block // 2).
		"""
		if new_index != self._resolution_index:
			blocksize = self._loop_selector._blocksize
			if anchor_time is None:
				anchor_time = (self._note_editor._get_effective_page() *
					blocksize * RESOLUTION_MAP[self._resolution_index])
			self._resolution_index = new_index
			new_resolution = RESOLUTION_MAP[new_index]
			self.set_resolution(new_resolution)
			# Block boundaries are powers of two: exact for on-grid times
			new_block = int(anchor_time // (blocksize * new_resolution))
			self._note_editor._select_block(new_block)
			# The loop selector row and the arrows follow the new block
			self._update_loop_selector()
			self._update_buttons()
		self._control_surface.show_message("RESOLUTION : " + RESOLUTION_NAMES[new_index])

	def _update_drum_group_device(self):
		# no drum rack mode for me. i am a melodic step seq.
		self._drum_group_device = None

	def _detect_scale_mode(self):
		# Manual mode: no device sniffing. Drumrack layout is whatever the
		# user chose in the scale selector — never overwrite it from here.
		self._sync_editor_layout()

	def _sync_editor_layout(self):
		"""
        Synchronize the entire editor layout with the Scale Selector's
        current Drum Rack state.
        ScaleComponent.is_drumrack is the source of truth.
        """
		if not hasattr(self, '_scale_selector') or not self._scale_selector:
			return
		if DEBUG_LOGGING:
			self._control_surface.log_message("[TRACE] _sync_editor_layout entered (selector dr=%s)" %
				getattr(self._scale_selector, '_is_drumrack', '?'))
		is_dr = bool(self._scale_selector.is_drumrack)

		# 1. Synchronize StepSequencer cached state
		old_is_dr = getattr(self, '_is_drumrack_mode', False)
		self._is_drumrack_mode = is_dr

		# 1b. Rebuild the row -> pitch map. The base _scale_updated() owns
		# _key_indexes / in_scale / root_note and derives them from the
		# selector's drumrack/diatonic state. Call the BASE method directly
		# The base _scale_updated() owns it and derives it from
		# ScaleComponent.is_diatonic, which is False whenever is_drumrack is True.
		# Call the BASE method; our override calls back into this one.
		# NOTE: in drumrack layout the note editor ignores _key_indexes and
		# maps rows to chromatic 8-note pitch blocks (see _row_pitch()).
		StepSequencerComponent._scale_updated(self)

		# 2. Synchronize Note Editor layout
		# Do this unconditionally.
		# During clip loading, _is_drumrack_layout may itself be stale.
		# on_drumrack_mode_changed() is responsible for rebuilding the
		# editor's note data and forcing the hardware refresh.

		note_editor = getattr(self, '_note_editor', None)
		if note_editor:
			# Mark that sync is happening, suppress redundant updates
			note_editor._layout_sync_pending = True
			note_editor.on_drumrack_mode_changed(is_dr)
			note_editor._layout_sync_pending = False  # Allow next update
			note_editor._force_update = True
			note_editor.update()  # Explicit update after sync

			if DEBUG_LOGGING:
				editor_is_dr = bool(
					getattr(note_editor, '_is_drumrack_layout', False)
				)
				self._control_surface.log_message(
					f"[LAYOUT_SYNC] "
					f"NoteEditor {editor_is_dr} -> {is_dr}"
				)


		# Synchronize Loop Selector ownership
		loop_selector = getattr(self, '_loop_selector', None)
		if loop_selector:
			if is_dr:
				loop_selector.set_enabled(False)
				if hasattr(loop_selector, '_buttons'):
					loop_selector._cache = [-1] * len(loop_selector._buttons)
					for button in loop_selector._buttons:
						if button:
							try:
								button.turn_off()
							except RuntimeError:
								pass
				loop_selector._force = True
			else:
				if self._loop_selector_should_be_enabled():
					loop_selector.set_enabled(True)
					if hasattr(loop_selector, '_get_clip_loop'):
						loop_selector._get_clip_loop()
					loop_selector._force = True
					loop_selector.update()
				else:
					loop_selector.set_enabled(False)

		# 4. Force complete redraw
		self._force_update = True
		if note_editor:
			note_editor._force_update = True
		self.update()

	def _scale_updated(self):
		# Synchronize the ownership BEFORE the parent update runs.
		# This is important: if super()._scale_updated() causes an update,
		# the parent must already know that Drum Rack owns row 7.
		self._sync_editor_layout()
		#super(StepSequencerComponent2, self)._scale_updated()

	# --- LOOP EDITED IN LIVE (loop_start / loop_end listeners) ---
	def _on_loop_changed(self):
		"""
		Called by the clip's loop_start / loop_end listeners, i.e. also when
		the loop is edited in Live itself (loop brace, clip view fields...).

		- Refreshes the loop selector (melodic row 7) and the note editor, so
		  the navigation view loop tint is current in BOTH layouts
		  (drumrack mode has no loop selector row to show it).
		- Rewrites the clip's SUX tag + JSON in BOTH layouts. The rewrite is
		  deferred and coalesced: Live does not allow renaming a clip from
		  inside a listener, and a loop drag fires many notifications.
		"""
		if self._clip is None:
			return
		ls = getattr(self, '_loop_selector', None)
		note_editor = getattr(self, '_note_editor', None)

		if self.is_enabled():
			if ls is not None:
				ls._get_clip_loop()
				if ls.is_enabled():
					ls._force = True
					ls.update()
			if note_editor is not None:
				note_editor._force_update = True
				note_editor.update()

		# Never write the tag while a clip is being loaded (the loader sets
		# the loop from the tag itself).
		if note_editor is not None and getattr(note_editor, '_loading_clip', False):
			return
		if getattr(self, '_loop_tag_sync_scheduled', False):
			return
		self._loop_tag_sync_scheduled = True
		try:
			self._control_surface.schedule_message(
				LOOP_TAG_SYNC_DELAY_TICKS, self._deferred_loop_tag_sync)
		except Exception:
			self._loop_tag_sync_scheduled = False

	def _deferred_loop_tag_sync(self):
		self._loop_tag_sync_scheduled = False
		note_editor = getattr(self, '_note_editor', None)
		if note_editor is None or self._clip is None:
			return
		# Only the clip both components agree on (a clip switch may have
		# happened during the delay).
		if getattr(note_editor, '_clip', None) != self._clip:
			return
		if getattr(note_editor, '_loading_clip', False):
			return
		try:
			self.sync_clip_with_json()
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					"[LOOP_EDIT_SYNC] tag updated loop=%s-%s" % (
						self._clip.loop_start, self._clip.loop_end))
		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[LOOP_EDIT_SYNC_ERROR] {e}")

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
				self._osd.attribute_names[3] = "Quantisation"
				if self._note_editor._is_monophonic:
					self._osd.attributes[4] = "Mono"
				else:
					self._osd.attributes[4] = "Poly"
				self._osd.attribute_names[4] = "Polyphony"
				self._osd.attribute_names[5] = "Mode"
				if self._note_editor._mode == STEPSEQ_MODE_NOTES and self._note_editor._is_focus_view:
					self._osd.attributes[5] = "Focus " + self._note_editor._pitch_name(
						self._note_editor._focus_pitch)
				elif self._note_editor._mode == STEPSEQ_MODE_NOTES:
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

	def sync_clip_with_json(self):
		"""
		CRITICAL: Call this EVERY TIME parameters change!
		Saves to JSON AND updates SUX tag in clip name.
		This is called from LoopSelectorComponent, MelodicNoteEditorComponent, etc.
		"""
		if hasattr(self, '_note_editor') and self._note_editor:
			try:
				if hasattr(self, '_loop_block') and hasattr(self, '_loop_selector'):
					ls = self._loop_selector
					if hasattr(ls, '_block'):
						self._loop_block = ls._block
				self._note_editor.sync_clip_with_json()
			except Exception as e:
				import traceback
				self._control_surface.log_message(f"[SYNC_ERROR] {e}\n{traceback.format_exc()}")
