#StepSequencerComponent2.py
from _Framework.ControlSurfaceComponent import ControlSurfaceComponent
from _Framework.ButtonMatrixElement import ButtonMatrixElement
from .StepSequencerComponent import StepSequencerComponent, ButtonElement
from .SequencerConstants import (RESOLUTION_NAMES, RESOLUTION_MAP,
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
import time
from random import uniform

import json
from pathlib import Path
import hashlib
import os                  # Used in _load_metadata_cache()
import re

LONG_BUTTON_PRESS = 1.0
BASE_OCTAVE = 8
DEBUG_LOGGING = True  # Set to False for release



class ClipMetadataManager:
	# =========================== CONSTANTS ===========================
	ID_TAG_PREFIX = '[SUX:'
	ID_TAG_SUFFIX = ']'

	def __init__(self, surface):
		self.surface = surface
		try:
			self.path = Path(surface.absolute_path) / "launchpad_clip_metadata.json"
		except AttributeError:
			self.path = Path(__file__).parent / "launchpad_clip_metadata.json"

		self.cache = {"clips": {}, "version": 1}

		# Track pending operations safely
		self._pending_renames = []  # [(clip_id, new_name, old_name)]
		self._renamed_clips = {}    # {clip_weak_ref_key: clip_id}

		self._undo_listener_registered = False

		self._load_cache()
		self._register_undo_listener()
		self._try_process_pending_operations()

	def _register_undo_listener(self):
		"""Register to listen for undo stack changes - fires between frames."""
		if self._undo_listener_registered:
			return

		try:
			song = self.surface.song()
			song.add_undo_stack_change_listener(self._on_undo_state_changed)
			self._undo_listener_registered = True
			if DEBUG_LOGGING:
				self.surface.log_message("[UNDO_LISTENER] Registered successfully")
		except Exception as e:
			self.surface.log_message(f"[UNDO_LISTENER] Registration failed: {e}")

	def _unregister_undo_listener(self):
		"""Clean up undo listener on disconnect."""
		if not self._undo_listener_registered:
			return

		try:
			song = self.surface.song()
			song.remove_undo_stack_change_listener(self._on_undo_state_changed)
			self._undo_listener_registered = False
		except:
			pass

	def _load_cache(self):
		if self.path.exists():
			try:
				with open(self.path, 'r') as f:
					loaded = json.load(f)
					if isinstance(loaded, dict) and "clips" in loaded:
						self.cache = loaded
					else:
						self.cache["clips"] = loaded
						self.cache["version"] = 1

				# Post-load migration check
				self._migrate_old_format()

				if DEBUG_LOGGING:
					self.surface.log_message(f"[CACHE_LOAD] Loaded {len(self.cache.get('clips', {}))} entries")
			except Exception as e:
				self.surface.log_message(f"[Cache Load Error] {e}")
				self.cache = {"clips": {}, "version": 1}
		else:
			self.cache = {"clips": {}, "version": 1}

	def _save_cache(self):
		"""Always save to disk immediately."""
		try:
			with open(self.path, 'w') as f:
				json.dump(self.cache, f, indent=2)
			if DEBUG_LOGGING:
				entry_count = len(self.cache.get("clips", {}))
				self.surface.log_message(f"[CACHE_SAVE] File updated, {entry_count} entries persisted")
		except Exception as e:
			self.surface.log_message(f"[Save Failed] {e}")

	def _migrate_old_format(self):
		"""Convert old flat JSON structure to new nested format."""
		clips_data = self.cache.get("clips", {})
		migrated = False

		for key, value in list(clips_data.items()):
			if isinstance(value, dict) and "settings" in value:
				continue  # Already in new format
			elif isinstance(value, dict):
				# Old flat format - wrap settings
				self.cache["clips"][key] = {
					"settings": value,
					"created_at": time.time(),
					"updated_at": time.time(),
					"track_index": -1,
					"slot_index": -1
				}
				migrated = True

		if migrated:
			self._save_cache()

	# =========================== UNDO STACK PROCESSING ===========================

	def _on_undo_state_changed(self):
		"""Fires after every Live operation - perfect timing for safe property changes."""
		self._try_process_pending_operations()

	def _try_process_pending_operations(self):
		"""Attempt to apply pending renames now that we're between Live callbacks."""
		processed = []

		for i, pending_op in reversed(list(enumerate(self._pending_renames))):
			clip_id, target_name, clip_object_id = pending_op

			try:
				# Retrieve original clip reference
				clip_obj = self._get_clip_by_object_id(clip_object_id)

				if not clip_obj or not hasattr(clip_obj, 'name'):
					# Clip was deleted or is invalid
					processed.append(i)
					continue

				# Safe to rename now!
				try:
					clip_obj.name = target_name
					processed.append(i)

					if DEBUG_LOGGING:
						display_name = target_name[:50] + ('...' if len(target_name) > 50 else '')
						self.surface.log_message(f"[RENAMED_APPLIED] '{display_name}'")
				except RuntimeError as re:
					# Still blocked - keep waiting for next undo event
					if DEBUG_LOGGING:
						self.surface.log_message(f"[RENAME_STILL_BLOCKED] {re} Will retry next undo event")

			except Exception as e:
				if DEBUG_LOGGING:
					self.surface.log_message(f"[RENAME_PROCESS_ERROR] {e}")
				processed.append(i)  # Drop this operation anyway

		# Remove processed items from queue
		for idx in sorted(processed, reverse=True):
			del self._pending_renames[idx]

		# Cleanup stale references
		self._cleanup_stale_clip_references()

	# =========================== CLIP IDENTIFICATION ===========================

	def ensure_clip_has_id(self, clip):
		"""Main entry point - ensures clip is tracked and has an ID."""
		if not clip:
			return None

		try:
			# Step 1: Try position-based identification (fast, reliable)
			primary_id = self._identify_clip_by_position(clip)

			if primary_id:
				# Validate ID exists in cache, create if missing
				if primary_id not in self.cache.get("clips", {}):
					self._create_or_validate_cache_entry(primary_id, clip)
				return primary_id

			# Step 2: Fallback - generate unique ID and mark for renaming
			temp_id = self._generate_temporary_id(clip)

			# Queue rename to happen during next undo event
			current_name = getattr(clip, 'name', '') or ''
			new_name = self.inject_clip_id_tag(current_name, temp_id)

			clip_object_id = id(clip)
			self._pending_renames.append((temp_id, new_name, clip_object_id))
			self._store_clip_reference(clip_object_id, clip)

			if DEBUG_LOGGING:
				self.surface.log_message(f"[QUEUED_RENAME] Will rename to '{new_name}' via undo callback")

			return temp_id

		except Exception as e:
			import traceback
			self.surface.log_message(f"[CLIP_ID_ERROR] {e}\n{traceback.format_exc()}")
			return None

	def _identify_clip_by_position(self, clip):
		"""Most stable identification method - uses track/slot position."""
		track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)

		if track_idx < 0 or slot_idx < 0:
			return None

		# Create composite ID from position
		content_hash = self._hash_clip_content(clip)
		position_based_id = f"POS_T{track_idx}_S{slot_idx}_{content_hash[:8]}"

		# Verify this matches cached entry by checking content hash hasn't changed
		if position_based_id in self.cache.get("clips", {}):
			cached = self.cache["clips"][position_based_id]
			cached_hash = cached.get("content_hash", "")
			if cached_hash == content_hash:
				return position_based_id  # Match confirmed

		# Hash mismatch or no entry - need fresh identification
		return position_based_id

	def _hash_clip_content(self, clip):
		"""Creates a fingerprint from clip's note structure."""
		note_signature = ""
		try:
			if hasattr(clip, 'notes'):
				notes = list(clip.notes)[:16]  # Limit for performance
				for n in notes:
					pitch = getattr(n, 'pitch', 0)
					time_pos = getattr(n, 'start_time', 0)
					duration = getattr(n, 'duration', 0)
					note_signature += f"{pitch}_{int(time_pos*100)}_{int(duration*100)}_"

			if note_signature:
				return hashlib.md5(note_signature.encode()).hexdigest()
			else:
				return "empty_clip"
		except:
			return "unknown_hash"

	def _get_current_track_slot_indices_safe(self, clip):
		"""Vector-safe location lookup."""
		if not clip:
			return -1, -1

		try:
			parent = clip.canonical_parent
			track = None
			while parent:
				p_type = type(parent).__name__
				if p_type == 'Track':
					track = parent
					break
				elif p_type == 'Song':
					break
				parent = getattr(parent, 'canonical_parent', None)

			if not track:
				return -1, -1

			song = None
			parent2 = track.canonical_parent
			while parent2:
				if type(parent2).__name__ == 'Song':
					song = parent2
					break
				parent2 = getattr(parent2, 'canonical_parent', None)

			if not song:
				return -1, -1

			tracks_list = list(song.tracks)
			track_index = -1
			for i, t in enumerate(tracks_list):
				if t == track:
					track_index = i
					break

			if track_index < 0:
				return -1, -1

			slot_index = -1
			clip_slots_list = list(track.clip_slots)
			for i, slot in enumerate(clip_slots_list):
				if slot.has_clip and getattr(slot, 'clip', None) == clip:
					slot_index = i
					break

			return track_index, slot_index

		except Exception as e:
			if DEBUG_LOGGING:
				self.surface.log_message(f"[LOCATION_INDEX_ERROR] {e}")
			return -1, -1

	def _create_or_validate_cache_entry(self, clip_id, clip):
		"""Create new entry or validate existing one."""
		if clip_id not in self.cache["clips"]:
			track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)

			self.cache["clips"][clip_id] = {
				"settings": {},
				"created_at": time.time(),
				"updated_at": time.time(),
				"track_index": track_idx,
				"slot_index": slot_idx,
				"track_name": "",
				"clip_name_original": getattr(clip, 'name', '').split(self.ID_TAG_PREFIX)[0].strip(),
				"content_hash": self._hash_clip_content(clip)
			}

			# Extract and store track name
			if track_idx >= 0:
				try:
					song = self.surface.song()
					tracks_list = list(song.tracks)
					if track_idx < len(tracks_list):
						self.cache["clips"][clip_id]["track_name"] = tracks_list[track_idx].name
				except:
					pass

			self._save_cache()

	def _generate_temporary_id(self, clip):
		"""Generate temporary ID for untracked clips."""
		import uuid
		content_hash = self._hash_clip_content(clip)
		return f"TMP_{uuid.uuid4().hex[:6]}_{content_hash[:4]}"

	# =========================== WEAK REFERENCE TRACKING ===========================

	def _store_clip_reference(self, object_id, clip):
		"""Store weak reference to clip for later retrieval."""
		import weakref
		try:
			weak_ref = weakref.ref(clip)
			self._renamed_clips[str(object_id)] = weak_ref
		except TypeError:
			# Some objects can't be weak-referenced - store object_id only
			self._renamed_clips[str(object_id)] = None

	def _get_clip_by_object_id(self, object_id):
		"""Retrieve clip from stored weak reference."""
		ref = self._renamed_clips.get(str(object_id))
		if ref is None:
			return None
		elif callable(ref):
			# It's a weakref
			return ref()
		else:
			return None

	def _cleanup_stale_clip_references(self):
		"""Remove expired weak references."""
		stale_keys = []
		for key, ref in self._renamed_clips.items():
			if ref is not None and callable(ref):
				if ref() is None:
					# Object was garbage collected
					stale_keys.append(key)

		for key in stale_keys:
			del self._renamed_clips[key]

	# =========================== RENAME UTILITIES ===========================

	def strip_clip_id_tag(self, clip_name):
		"""Removes [LUX:id] suffix from clip name."""
		if not clip_name:
			return clip_name, None

		last_bracket = clip_name.rfind(self.ID_TAG_SUFFIX)
		if last_bracket != -1:
			prefix = clip_name[:last_bracket].strip()
			potential_tag = clip_name[last_bracket:]
			if potential_tag.startswith(self.ID_TAG_PREFIX) and len(potential_tag) > len(self.ID_TAG_PREFIX) + len(self.ID_TAG_SUFFIX):
				extracted_id = potential_tag[len(self.ID_TAG_PREFIX):-len(self.ID_TAG_SUFFIX)]
				return prefix, extracted_id
		return clip_name, None

	def inject_clip_id_tag(self, clip_name, clip_id):
		"""Adds [LUX:id] suffix to clip name."""
		if not clip_name:
			return f"{self.ID_TAG_PREFIX}{clip_id}{self.ID_TAG_SUFFIX}"

		existing_name, existing_id = self.strip_clip_id_tag(clip_name)
		if existing_id:
			return clip_name  # Already tagged

		return f"{existing_name} {self.ID_TAG_PREFIX}{clip_id}{self.ID_TAG_SUFFIX}"

	def generate_new_clip_id(self):
		"""Generates a unique hex ID for a clip."""
		import uuid
		return uuid.uuid4().hex[:8]

	# =========================== SYNC & CLEANUP ===========================

	def synchronize_all_clip_locations(self):
		"""Call this when tracks/scenes change to update all cached locations AND remove orphans."""
		clips_dict = self.cache.get("clips", {})
		total_entries = len(clips_dict)
		updated_count = 0
		deleted_count = 0

		try:
			song = self.surface.song()
			existing_clip_ids = set()

			# === SCAN ALL CURRENT CLIPS IN THE SONG ===
			for track_idx, track in enumerate(list(song.tracks)):
				for slot_idx, slot in enumerate(list(track.clip_slots)):
					if slot.has_clip:
						clip = slot.clip

						# Check if this clip has our ID tag
						_, clip_id = self.strip_clip_id_tag(getattr(clip, 'name', ''))

						if clip_id:
							existing_clip_ids.add(clip_id)

							# Update location info if changed
							if clip_id in clips_dict:
								entry = clips_dict[clip_id]
								old_track = entry.get("track_index", -1)
								old_slot = entry.get("slot_index", -1)

								if track_idx != old_track or slot_idx != old_slot:
									entry["track_index"] = track_idx
									entry["slot_index"] = slot_idx
									entry["updated_at"] = time.time()

									# Update track name
									try:
										entry["track_name"] = track.name
									except:
										pass

									updated_count += 1
									if DEBUG_LOGGING:
										self.surface.log_message(
											f"[SYNC_MOVE] {clip_id}: T{old_track}:{old_slot} → T{track_idx}:{slot_idx}"
										)
						else:
							# Clip without tag - check by content hash for legacy support
							content_hash = self._hash_clip_content(clip)
							for cid, entry in list(clips_dict.items()):
								if entry.get("content_hash") == content_hash and \
										entry.get("clip_name_original", "").lower() == \
										getattr(clip, 'name', '').lower():
									# Found match - add tag now!
									existing_clip_ids.add(cid)
									new_name = self.inject_clip_id_tag(clip.name, cid)

									clip_object_id = id(clip)
									self._pending_renames.append((cid, new_name, clip_object_id))
									self._store_clip_reference(clip_object_id, clip)

									if DEBUG_LOGGING:
										self.surface.log_message(f"[SYNC_ADD_TAG] Added tag to '{clip.name}'")
									break

			# === FIND ORPHANED ENTRIES (CLIPS THAT WERE DELETED) ===
			# Only delete TEMP_/FALLBACK_ entries by default to prevent data loss
			# Uncomment below line if you want AGGRESSIVE cleanup (dangerous!)
			# all_orphaned = [cid for cid in clips_dict if cid not in existing_clip_ids]

			orphaned_ids = []
			for cid in clips_dict:
				if cid not in existing_clip_ids:
					# Safety: Only auto-delete untagged/temporary entries
					if cid.startswith("TMP_") or cid.startswith("FALLBACK_"):
						orphaned_ids.append(cid)
						deleted_count += 1
					elif DEBUG_LOGGING:
						# Warn about potential orphan but don't delete
						self.surface.log_message(
							f"[ORPHAN_DETECTED] {cid} exists in cache but clip not found (preserved for safety)"
						)

			# Remove identified orphans
			for oid in orphaned_ids:
				del clips_dict[oid]

			# Save if anything changed
			if updated_count > 0 or deleted_count > 0:
				self._save_cache()
				if DEBUG_LOGGING:
					self.surface.log_message(
						f"[SYNC_COMPLETE] Updated {updated_count}/{total_entries}, removed {deleted_count} orphans"
					)

		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self.surface.log_message(f"[SYNC_ERROR] {e}\n{traceback.format_exc()}")

	# =========================== SAVE / LOAD SETTINGS ===========================

	def get_settings(self, clip):
		"""Retrieve settings for clip."""
		clip_id = self.ensure_clip_has_id(clip)
		if clip_id and clip_id in self.cache.get("clips", {}):
			return self.cache["clips"][clip_id].get("settings", {})
		return None

	def save_settings(self, clip, settings_dict):
		"""Save settings for clip."""
		if not clip:
			return False

		clip_id = self.ensure_clip_has_id(clip)  # Returns clip_id or None

		if clip_id and clip_id in self.cache.get("clips", {}):
			try:
				self.cache["clips"][clip_id]["settings"] = settings_dict
				self.cache["clips"][clip_id]["updated_at"] = time.time()

				# Also sync location
				track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)
				if track_idx >= 0 and slot_idx >= 0:
					self.cache["clips"][clip_id]["track_index"] = track_idx
					self.cache["clips"][clip_id]["slot_index"] = slot_idx

				self._save_cache()
				return True
			except KeyError as ke:
				self.surface.log_message(f"[SAVE_KEY_ERROR] {ke}")
				return False
		else:
			self.surface.log_message("[SAVE_WARN] Clip entry not found, cannot save")
			return False

	def save_clip_to_json(self, clip, params_dict):
		"""
        Save clip settings to JSON with multiple lookup keys for robustness.

        Creates THREE reference points:
        1. By original creation position (stable historical reference)
        2. By current position (fast runtime lookup)
        3. By content hash (verification/recovery anchor)
        """
		if not clip or not hasattr(clip, 'name'):
			return False

		try:
			# Get identifiers
			track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)
			content_hash = self._hash_clip_content(clip)

			# Generate stable unique key
			base_key = f"{track_idx}:{slot_idx}"
			hash_suffix = content_hash[:12] if content_hash else "unknown"

			# Three parallel keys for maximum flexibility
			original_position_key = f"ORIG_{base_key}_{hash_suffix}"
			current_position_key = f"CURRENT_{base_key}_{hash_suffix}"
			content_only_key = f"HASH_{hash_suffix}"

			# Build unified cache entry
			entry = {
				"settings": params_dict,
				"track_index": track_idx,
				"slot_index": slot_idx,
				"content_hash": content_hash,
				"clip_name_last_seen": getattr(clip, 'name', '').split(' [')[0].strip() if ' [' in getattr(clip, 'name',
				                                                                                           '') else getattr(
					clip, 'name', ''),
				"original_creation_pos": f"T{track_idx}_S{slot_idx}",
				"current_pos": f"T{track_idx}_S{slot_idx}",
				"created_at": time.time(),
				"updated_at": time.time(),
				"metadata_version": 2  # Increment when format changes
			}

			# Store under all three keys for flexible lookup
			clips_dict = self.cache.setdefault("clips", {})
			clips_dict[original_position_key] = entry
			clips_dict[current_position_key] = entry

			# For content-only key, only keep ONE entry per hash (avoid bloat)
			clips_dict[content_only_key] = entry

			# Cleanup stale position keys (older than 5 minutes)
			self._prune_outdated_position_keys(track_idx, slot_idx, hash_suffix)

			# Persist
			self._save_cache()

			if DEBUG_LOGGING:
				self.surface.log_message(
					f"[JSON_SAVE] Stored clip under 3 keys (O/C/H) at T{track_idx}:S{slot_idx}"
				)

			return True

		except Exception as e:
			self.surface.log_message(f"[JSON_SAVE_ERROR] {e}")
			return False

	def _prune_outdated_position_keys(self, track_idx, slot_idx, hash_suffix):
		"""Remove obsolete position references to prevent JSON bloat."""
		clips_dict = self.cache.get("clips", {})
		stale_keys = []

		now = time.time()
		age_threshold = 300  # 5 minutes

		for key in list(clips_dict.keys()):
			if key.startswith("POSITION_") and f"{track_idx}:{slot_idx}" in key:
				entry = clips_dict[key]
				if now - entry.get("updated_at", 0) > age_threshold:
					stale_keys.append(key)

		for key in stale_keys:
			del clips_dict[key]

	def recover_clip_settings_from_json(self, clip):
		"""
        Attempt to find stored settings in JSON despite missing/corrupted embedded tag.

        Priority order:
        1. Exact content-hash match (most reliable)
        2. Track+slot match PLUS content-hash verification
        3. Track+slot match ONLY (fallback - may need warnings)
        """
		if not clip:
			return None

		try:
			# Current clip identifiers
			content_hash = self._hash_clip_content(clip)
			track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)
			clips_dict = self.cache.get("clips", {})

			# === STRATEGY 1: Exact content hash match ===
			hash_key = f"HASH_{content_hash[:12]}"
			if hash_key in clips_dict:
				entry = clips_dict[hash_key]
				if DEBUG_LOGGING:
					self.surface.log_message(
						f"[RECOVERY_SUCCESS] Found via HASH_MATCH for T{track_idx}:S{slot_idx}"
					)
				return entry.get("settings")

			# === STRATEGY 2: Position + hash verification ===
			pos_keys = [key for key in clips_dict.keys() if f"T{track_idx}_S{slot_idx}" in key]

			for key in pos_keys:
				entry = clips_dict[key]
				if entry.get("content_hash") == content_hash:
					if DEBUG_LOGGING:
						self.surface.log_message(
							f"[RECOVERY_SUCCESS] Found via POS+HASH for T{track_idx}:S{slot_idx}"
						)
					return entry.get("settings")

			# === STRATEGY 3: Position match ONLY (WARNING needed) ===
			if pos_keys:
				most_recent = max(pos_keys, key=lambda k: clips_dict[k].get("updated_at", 0))
				entry = clips_dict[most_recent]

				# WARNING: Could be wrong clip now residing in same slot!
				if DEBUG_LOGGING:
					self.surface.log_message(
						f"[RECOVERY_WARNING] Found via POSITION_ONLY (potential false positive)"
					)

				# Optional: Store flag indicating "needs verification"
				# We'll pass this info to caller via wrapper object

				# Only return if user accepts risk (caller decides)
				return {
					"settings": entry.get("settings"),
					"requires_verification": True,
					"warning": f"This clip occupies slot T{track_idx}:S{slot_idx} but content differs from last saved snapshot."
				}

			# === NOTHING FOUND ===
			if DEBUG_LOGGING:
				self.surface.log_message(f"[RECOVERY_FAILED] No matching JSON entries found")
			return None

		except Exception as e:
			self.surface.log_message(f"[RECOVERY_ERROR] {e}")
			return None

	def update_clip_location_in_json(self, clip):
		"""Update JSON entry when clip moves to new track/slot."""
		if not clip:
			return False

		try:
			# Find existing entry by content hash
			content_hash = self._hash_clip_content(clip)
			hash_key = f"HASH_{content_hash[:12]}"
			clips_dict = self.cache.get("clips", {})

			if hash_key not in clips_dict:
				# New clip - save fresh entry
				params_dict = {
					'scale': 0, 'root_note': 0, 'display_octave': 2,
					'resolution_index': 4, 'loop_block': 0, 'loop_page_offset': 0,
					'clip_loop_start': 0.0, 'clip_loop_end': 16.0
				}
				self.save_clip_to_json(clip, params_dict)
				return True

			# Existing clip - update position tracking
			entry = clips_dict[hash_key]
			track_idx, slot_idx = self._get_current_track_slot_indices_safe(clip)

			# Update both current and original position records
			entry["track_index"] = track_idx
			entry["slot_index"] = slot_idx
			entry["current_pos"] = f"T{track_idx}_S{slot_idx}"
			entry["updated_at"] = time.time()

			# Optionally update clip name reference if it changed
			current_name = getattr(clip, 'name', '').split(' [')[0].strip()
			if current_name != entry.get("clip_name_last_seen"):
				entry["clip_name_last_seen"] = current_name

			self._save_cache()

			if DEBUG_LOGGING:
				self.surface.log_message(
					f"[LOCATION_UPDATE] Moved to T{track_idx}:S{slot_idx} (hash verified)"
				)

			return True

		except Exception as e:
			self.surface.log_message(f"[LOCATION_UPDATE_ERROR] {e}")
			return False


	def remove_clip_entry(self, clip):
		"""Remove entry when clip is deleted."""
		clip_id, _ = self.strip_clip_id_tag(getattr(clip, 'name', ''))
		if clip_id and clip_id in self.cache.get("clips", {}):
			del self.cache["clips"][clip_id]
			self._save_cache()
			if DEBUG_LOGGING:
				self.surface.log_message(f"[CLIP_DELETED] Removed entry for '{clip_id}'")

	def _run_cleanup_check(self):
		"""Lightweight verification - no heavy cleanup (done via observers)."""
		if not hasattr(self, '_meta_manager') or not self._meta_manager:
			return

		try:
			# Trigger sync scan (includes orphan detection)
			self._meta_manager.synchronize_all_clip_locations()

			if DEBUG_LOGGING:
				self._control_surface.log_message("[VERIFICATION_SYNC] Completed background check")

			# Reschedule next check (every 5 minutes for safety)
			if hasattr(self._control_surface, 'schedule_message'):
				self._control_surface.schedule_message(10, self._run_cleanup_check)

		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[CHECK_ERROR] {e}")

			# Still reschedule
			if hasattr(self._control_surface, 'schedule_message'):
				self._control_surface.schedule_message(10, self._run_cleanup_check)


	def _schedule_cleanup_check(self):
		"""Schedule next cleanup check in 60 seconds."""
		if hasattr(self._control_surface, 'schedule_message'):
			self._control_surface.schedule_message(10, self._run_cleanup_check)



	def cleanup_orphans(self, current_keys=None):
		"""Remove orphaned entries from cache (clips no longer exist)."""
		if current_keys is None:
			# Collect all current valid clip IDs automatically
			try:
				song = self.surface.song()
				current_keys = set()
				for track_idx, track in enumerate(list(song.tracks)):
					for slot_idx, slot in enumerate(list(track.clip_slots)):
						if slot.has_clip:
							clip = slot.clip
							_, clip_id = self.strip_clip_id_tag(getattr(clip, 'name', ''))
							if clip_id:
								current_keys.add(clip_id)
			except Exception as e:
				if DEBUG_LOGGING:
					self.surface.log_message(f"[CLEANUP_KEYS_ERROR] {e}")
				return

		# Scan cache and collect orphans
		orphans = []
		cleaned = False

		clips_dict = self.cache.get("clips", {})
		for cid in list(clips_dict.keys()):
			if cid not in current_keys:
				# Only delete TEMP_ prefixed entries OR explicitly confirm deletion
				# This prevents accidental loss of manually-created clips
				if cid.startswith("TMP_") or cid.startswith("FALLBACK_"):
					orphans.append(cid)
					cleaned = True

		# Remove orphans
		for oid in orphans:
			del clips_dict[oid]

		if cleaned:
			self._save_cache()
			if DEBUG_LOGGING:
				self.surface.log_message(f"[CLEANUP_ORPHANS] Removed {len(orphans)} entries: {orphans}")

		return len(orphans)


class MelodicNoteEditorComponent(ControlSurfaceComponent):

	def __init__(self, step_sequencer, matrix, side_buttons, control_surface):
		self._initializing = True
		ControlSurfaceComponent.__init__(self)
		self._control_surface = control_surface
		self.set_enabled(False)
		# ---------------------------------------------------------
		# METADATA MANAGER SETUP
		# ---------------------------------------------------------
		# We instantiate the manager which handles path resolution and caching internally.
		# It logs the exact path it's trying to use.
		try:
			self._meta_manager = ClipMetadataManager(control_surface)
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[META] Manager initialized. Cache size: {len(self._meta_manager.cache)}")

			# Register observers for automatic sync
			self._setup_observer_listeners()
		except Exception as e:
			self._control_surface.log_message(f"[META_INIT_ERROR] {e}")
			self._meta_manager = None
		# if self._meta_manager and DEBUG_LOGGING:
		# 	test_clip_id = f"TEST::{self._meta_manager.get_key(None)}::0"
		# 	# Create a fake clip object or just test the path writing directly
		# 	try:
		# 		# Directly test writing to the path
		# 		with open(self._meta_manager.path, 'w') as f:
		# 			json.dump({"test": "write_success", "path": str(self._meta_manager.path)}, f)
		# 		self._control_surface.log_message(f"[META TEST] SUCCESS wrote to: {self._meta_manager.path}")
		# 	except Exception as e:
		# 		self._control_surface.log_message(f"[META TEST] FAILED to write: {e}")
		# ---------------------------------------------------------

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

		# octaves
		self._display_octave = 2
		self._overview_start_octave = 2  # Default start: Rows 7-0 will show 2-9
		self._last_displayed_octave = 2
		self._scroll_pending_action = False

		# quantization
		#self._quantization = 16

		# Resolution
		self._resolution = 16
		# MODE
		self._mode = STEPSEQ_MODE_NOTES

		# Clip on/off
		self._clip_toggle_button = None
		self.set_clip_toggle_button(self._side_buttons[2])

		# Copy/paste/delete
		self._mode_copy_paste_button = None
		self.set_mode_copy_paste_button(self._side_buttons[3])
		self._copied_grid_data = None  # Stores (Pitch, Time) tuples when armed
		self._paste_armed = False      # True if waiting for paste command
		# if DEBUG_LOGGING:
		# 	self._control_surface.log_message("[INIT] CopyPaste Component Initialized. Arming=False, Data=None")
		self._last_press_time = 0.0  # Timestamp for double-click detection
		self._suppress_toggle_release = False
		self._delete_action_on_release = False
		self._skip_copy_release = False
		self._duplicate_pending = False
		self._update_copy_paste_button_state()

		# Length mode
		self._mode_notes_lengths_button = None
		self.set_mode_notes_lengths_button(self._side_buttons[4])
		self._is_notes_lengths_shifted = False
		self._last_notes_lengths_button_press = time.time()
		self._mode_notes_velocities_button = None

		# Velocity mode
		self.set_mode_notes_velocities_button(self._side_buttons[5])
		self._is_notes_velocity_shifted = False
		self._last_notes_velocity_button_press = time.time()
		self._mode_notes_octaves_button = None

		# Octave mode
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

		# Final initialization of button state
		if DEBUG_LOGGING:
			self._control_surface.log_message("[INIT] Completing button setup")
		self._update_copy_paste_button_state()

		# ============================================
		# BACKGROUND FLUSH TIMER
		# ============================================
		# Regularly attempt to flush pending renames every few seconds
		if DEBUG_LOGGING:
			self._control_surface.log_message("[FLUSH_TIMER] Initializing background flush timer")

		try:
			self._flush_timer_active = True
			self._schedule_background_flush()
		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[FLUSH_TIMER_INIT_ERROR] {e}")

		# CRITICAL: Ensure hardware receives the command immediately
		if hasattr(self, '_force_update'):
			self._force_update = True
		# if hasattr(self, 'update'):
		# 	# Call update to push all lights including the copy button
		# 	# But avoid recursion if matrix isn't ready
		# 	if matrix is not None:
		# 		for x in range(8):
		# 			for y in range(8):
		# 				button = self._matrix.get_button(x, y)
		# 				if button:
		# 					try:
		# 						# Force each button to send its current state
		# 						pass  # _update_matrix will handle this
		# 					except RuntimeError:
		# 						pass

		# end init
		self._initializing = False

		# Final safety push
		if DEBUG_LOGGING:
			self._control_surface.log_message("[INIT] Initial state complete")

	def disconnect(self):
		"""
        Clean up resources and FLUSH ALL PENDING RENAMES before exit.

        CRITICAL: This ensures SUX tags get written to clips even if
        they were queued and never executed due to lack of undo activity.
        """
		# STOP BACKGROUND FLUSH TIMER FIRST
		if hasattr(self, '_flush_timer_active'):
			self._flush_timer_active = False

		if DEBUG_LOGGING:
			self._control_surface.log_message("[DISCONNECT_START] Stopping timers and beginning cleanup sequence")

		# ============================================
		# PHASE 1: SAVE ACTIVE CLIP STATE TO JSON
		# ============================================
		if self._meta_manager and self._clip:
			try:
				old_state = self._get_current_state_dict()
				self._meta_manager.save_clip_to_json(self._clip, old_state)
				if DEBUG_LOGGING:
					self._control_surface.log_message("[DISCONNECT] Saved active clip state to JSON")
			except Exception as e:
				self._control_surface.log_message(f"[PREV_CLIP_SAVE_ERROR] {e}")

		# ============================================
		# PHASE 2: FORCIBLEY FLUSH PENDING RENAMES
		# ============================================
		# This is the KEY FIX - attempt to write any queued tags now!
		self._flush_pending_renames()

		# ============================================
		# PHASE 3: TRY ONE MORE ROUND OF VERIFICATION
		# ============================================
		# Double-check that no renames are still stuck
		if self._meta_manager and self._meta_manager._pending_renames:
			if DEBUG_LOGGING:
				remaining = len(self._meta_manager._pending_renames)
				self._control_surface.log_message(
					f"[DISCONNECT_WARNING] {remaining} renames still queued - may be lost!"
				)

				# Attempt second flush
				second_flush = self._flush_pending_renames()
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[DISCONNECT_SECOND_FLUSH] Additional {second_flush} processed"
					)

		# ============================================
		# PHASE 4: REMOVE OBSERVERS AND LISTENERS
		# ============================================
		self._remove_observer_listeners()

		# ============================================
		# PHASE 5: UNREGISTER META MANAGER UNDO LISTENER
		# ============================================
		if hasattr(self, '_meta_manager') and self._meta_manager:
			self._meta_manager._unregister_undo_listener()

		# ============================================
		# PHASE 6: CLEAN UP OTHER RESOURCES
		# ============================================
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

		if DEBUG_LOGGING:
			self._control_surface.log_message("[DISCONNECT_COMPLETE] All cleanup finished")
	
	
	# def _remove_scale_listeners(self):
	# 	try:
	# 		if self.song():
	# 			self.song().remove_root_note_listener(self.handle_root_note_changed)
	# 			self.song().remove_scale_name_listener(self.handle_scale_name_changed)
	# 	except RuntimeError:
	# 		pass

	@property
	def resolution_beats(self):
		"""SAFE accessor - always returns valid beat value ≥ 0.001."""
		val = getattr(self, '_resolution', 0.25)

		# Handle case where accidentally stored as INDEX (0-7) instead of beats
		if isinstance(val, (int, float)):
			if val <= 0:
				return 0.25  # Invalid, return safe default

			# If looks like index (small integer), convert using RESOLUTION_MAP
			if val >= 0 and val < 8 and isinstance(val, int):
				from .SequencerConstants import RESOLUTION_MAP
				try:
					converted = float(RESOLUTION_MAP[val])
					if converted > 0:
						return converted
				except:
					pass

			# Assume already beat value (our desired state)
			return max(0.001, val)

		# Unknown type - safest fallback
		return 0.25

	# =============================================================
	# EMBEDDED METADATA TAG SYSTEM - PURE SUX FORMAT
	# =============================================================

	METADATA_PREFIX = "[SUX:"  # New universal tag format
	METADATA_SUFFIX = "]"

	def _schedule_background_flush(self):
		"""Schedule the next background flush attempt."""
		if not hasattr(self, '_flush_timer_active') or not self._flush_timer_active:
			return

		# Schedule check in ~2 seconds
		if hasattr(self._control_surface, 'schedule_message'):
			try:
				self._control_surface.schedule_message(15, self._background_flush_callback)
			except Exception as e:
				if DEBUG_LOGGING:
					self._control_surface.log_message(f"[FLUSH_SCH_ERR] {e}")

	def _background_flush_callback(self):
		"""Background task that periodically flushes pending renames."""
		if not hasattr(self, '_flush_timer_active') or not self._flush_timer_active:
			return

		# Perform the actual flush
		count = self._flush_pending_renames()

		# Check if we still have items pending
		if hasattr(self, '_meta_manager') and self._meta_manager:
			remaining = len(self._meta_manager._pending_renames)

			if DEBUG_LOGGING:
				if remaining > 0:
					self._control_surface.log_message(
						f"[BACKGROUND_FLUSH] Flushed {count}, {remaining} remain"
					)

				# If nothing remaining, maybe stop frequent checks
				if remaining == 0 and count == 0:
					# Quiet period - don't spam logs
					pass

		# Always reschedule unless we're shutting down
		self._schedule_background_flush()

	def extract_embedded_parameters(self, clip_name):
		"""
        Extract parameter array from clip name tag [SUX:{scale;root;oct;res;blk;off;start;end}].

        Args:
            clip_name (str): Full clip name

        Returns:
            dict: Parsed parameters or None if no valid tag found
        """
		if not clip_name:
			return None

		try:
			# Find tag
			start_tag = clip_name.find(self.METADATA_PREFIX + "{")
			if start_tag == -1:
				return None

			end_tag = clip_name.rfind("}")
			if end_tag <= start_tag:
				return None

			param_string = clip_name[start_tag + len("{"): end_tag].strip()

			# Parse semicolon-separated values
			if ";" in param_string:
				values = param_string.split(";")

				# Ensure minimum length
				while len(values) < 8:
					values.append("0")

				params = {
					'scale': int(float(values[0])) % 12 if values[0] else 0,
					'root_note': int(float(values[1])) % 12 if values[1] else 0,
					'display_octave': max(0, min(15, int(float(values[2])))) if values[2] else 2,
					'resolution_index': max(0, min(7, int(float(values[3])))) if values[3] else 4,
					'loop_block': int(float(values[4])) if values[4] else 0,
					'loop_page_offset': int(float(values[5])) if values[5] else 0,
					'clip_loop_start': float(values[6]) if values[6] else 0.0,
					'clip_loop_end': float(values[7]) if values[7] else 16.0
				}

				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_EXTRACT] Loaded params from SUX tag in clip name"
					)
				return params

			return None

		except (ValueError, IndexError) as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[TAG_EXTRACT_ERROR] {e}")
			return None

	def build_embedded_parameters_tag(self, params_dict):
		"""
	    Construct the SUX metadata tag from parameter dictionary.

	    Format: [SUX:{scale;root_note;display_octave;resolution_index;loop_block;loop_page_offset;clip_loop_start;clip_loop_end}]

	    IMPORTANT: Must include BOTH opening '[' and closing ']' brackets!

	    Returns:
	        str: Formatted tag like "[SUX:{0;0;2;4;0;0;0.0;16.0}]"
	    """
		try:
			values = [
				str(int(params_dict.get('scale', 0))),
				str(int(params_dict.get('root_note', 0))),
				str(int(params_dict.get('display_octave', 2))),
				str(int(params_dict.get('resolution_index', 4))),
				str(int(params_dict.get('loop_block', 0))),
				str(int(params_dict.get('loop_page_offset', 0))),
				str(round(float(params_dict.get('clip_loop_start', 0.0)), 1)),
				str(round(float(params_dict.get('clip_loop_end', 16.0)), 1))
			]

			param_string = ";".join(values)

			# CRITICAL FIX: Include BOTH opening '[' and closing ']'
			full_tag = f"{self.METADATA_PREFIX}{{{param_string}}}{self.METADATA_SUFFIX}"

			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[BUILD_TAG_DEBUG] Built tag: '{full_tag}'"
				)

			return full_tag

		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[BUILD_TAG_ERROR] {e}")
			# Return minimal valid tag format with BOTH brackets
			return f"{self.METADATA_PREFIX}{{0;0;2;4;0;0;0.0;16.0}}{self.METADATA_SUFFIX}"

	def update_clip_name_with_params(self, clip, params_dict):
		"""
		Update clip name with SUX parameter tag.

		STRATEGY:
		1. CHECK if valid tag already exists
		2. Attempt IMMEDIATE write first
		3. If Live blocks it, use schedule_message() for proper deferral (NOT just queueing)
		4. Still maintain backup queue for disconnect flush

		Returns True if tag was initiated (immediately or deferred), False on critical failure.
		"""
		if not clip or not hasattr(clip, 'name'):
			if DEBUG_LOGGING:
				self._control_surface.log_message("[NAME_UPDATE] No clip/name available")
			return False

		try:
			# Build the desired tag
			new_tag = self.build_embedded_parameters_tag(params_dict)
			original_name = getattr(clip, 'name', '')

			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[TAG_DEBUG] Original clip name: '{original_name[:60]}'" +
					("..." if len(original_name) > 60 else "")
				)

			clean_name = self.strip_metadata_tags(original_name)

			# ============================================
			# PROPER TAG VERIFICATION (FIXED VERSION)
			# ============================================
			tag_exists_and_valid = False

			if "[SUX:" in original_name:
				# Look for COMPLETE tag structure with BOTH brackets
				start_bracket = original_name.find("[SUX:")
				end_bracket = original_name.rfind("]")

				if start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
					# Extract the candidate tag INCLUDING closing bracket
					potential_tag = original_name[start_bracket:end_bracket + 1]

					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[TAG_DETECT] Found candidate: '{potential_tag}'"
						)

					# Verify complete structure: must have [SUX:, {, ;, }, ]
					has_open = "[" in potential_tag and "{" in potential_tag
					has_semicolon = ";" in potential_tag
					has_close_curly = "}" in potential_tag
					has_close_square = "]" in potential_tag

					if has_open and has_semicolon and has_close_curly and has_close_square:
						tag_exists_and_valid = True

						if DEBUG_LOGGING:
							self._control_surface.log_message(
								f"[TAG_VALID] ✓ Complete tag structure confirmed"
							)
					else:
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								f"[TAG_INVALID] Tag missing components: " +
								f"open={has_open};semi={has_semicolon};curly]={has_close_curly};square]={has_close_square}"
							)

			if tag_exists_and_valid:
				# Check if parameters match what we'd write
				old_clean = clean_name
				expected_full_name = f"{old_clean} {new_tag}" if old_clean else new_tag

				if self.strip_metadata_tags(original_name) == self.strip_metadata_tags(expected_full_name):
					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[TAG_UP_TO_DATE] Clip name already contains correct parameters"
						)
					return True

			if tag_exists_and_valid:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_PARAMS_MISMATCH] Tags differ, updating..."
					)

			# Build new full name
			if clean_name:
				new_full_name = f"{clean_name} {new_tag}"
			else:
				new_full_name = new_tag

			if DEBUG_LOGGING:
				display_new = new_full_name[:50] + ('...' if len(new_full_name) > 50 else '')
				self._control_surface.log_message(
					f"[TAG_PREPARED] Building name: '{display_new}'"
				)

			# ============================================
			# PHASE 1: TRY IMMEDIATE WRITE FIRST
			# ============================================
			immediate_success = False
			try:
				clip.name = new_full_name
				immediate_success = True

				if DEBUG_LOGGING:
					display_written = new_full_name[:50] + ('...' if len(new_full_name) > 50 else '')
					self._control_surface.log_message(
						f"[TAG_IMMEDIATE_SUCCESS] Written directly: '{display_written}'"
					)

				# VERIFY it actually took effect
				actual_name = getattr(clip, 'name', '')

				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[TAG_VERIFY] Actual clip name after write: '{actual_name[:60]}'" +
						("..." if len(actual_name) > 60 else "")
					)

				if new_tag in actual_name:
					if DEBUG_LOGGING:
						self._control_surface.log_message(f"[TAG_VERIFIED] ✓ Tag confirmed in clip name")
					return True
				elif "[SUX:" in actual_name and "}" in actual_name:
					if DEBUG_LOGGING:
						self._control_surface.log_message(f"[TAG_PARTIAL] Tag exists but may vary slightly")
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

			# ============================================
			# PHASE 2: SCHEDULE MESSAGE DEFERRAL (KEY FIX!)
			# ============================================
			if not immediate_success:
				# IMPORTANT: Use schedule_message() for proper Living Framework deferral
				# This tells Live "try this again after current callback chain completes"

				# Store the rename request data
				rename_request = {
					'clip_object': clip,
					'target_name': new_full_name,
					'timestamp': time.time()
				}

				# Store temporarily for scheduled callback access
				self._pending_deferred_rename = rename_request

				# Schedule on next frame
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
				return False

			return True

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
			if DEBUG_LOGGING:
				self._control_surface.log_message("[DEFERRED_SKIP] Clip invalid")
			return

		# Try to write now - callback chain should be complete
		try:
			clip.name = target_name

			# Verify
			actual_name = getattr(clip, 'name', '')
			if "[SUX:" in actual_name:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[DEFERRED_SUCCESS] Tag written via delayed callback"
					)
				return

			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[DEFERRED_VERIFY_WARN] Deferred write succeeded but tag may not be present"
				)

		except RuntimeError as re:
			# Still blocked - fall back to traditional queue
			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[DEFERRED_BLOCKED] Still blocked after delay: {re}. Queuing permanently..."
				)

			# Move to persistent queue
			if hasattr(self, '_meta_manager') and self._meta_manager:
				clip_object_id = id(clip)
				self._meta_manager._pending_renames.append((None, target_name, clip_object_id))
				self._meta_manager._store_clip_reference(clip_object_id, clip)

		return

	def strip_metadata_tags(self, clip_name):
		"""
		Remove all SUX metadata tags from clip name for cleanliness.

		Args:
			clip_name (str): Raw clip name

		Returns:
			str: Name without [SUX:...}] tags
		"""
		if not clip_name:
			return clip_name

		# Step 1: Remove well-formed tags at end of name
		cleaned = re.sub(r'\s+\[SUX:\{[^}]*\}\]$', '', clip_name).strip()

		# Step 2: If nothing changed, try removing malformed/incomplete tags
		if cleaned == clip_name:
			# Remove trailing [SUX:...anything...] patterns
			cleaned = re.sub(r'\s+\[SUX:[^\]]*$', '', clip_name).strip()

		# Step 3: One final sweep for any stray tag fragments
		if '[SUX:' in cleaned:
			# Conservative approach: only remove if clearly at end
			last_bracket = cleaned.rfind('[SUX:')
			if last_bracket > 0:
				cleaned = cleaned[:last_bracket].strip()

				return cleaned

	def _flush_pending_renames(self):
		"""
	    FORCE-FLUSH all pending rename operations immediately.

	    Call this during disconnect() before script shutdown to ensure
	    any queued tags get written to clip names before Live exits.

	    Returns count of successfully processed renames.
	    """
		if not hasattr(self, '_meta_manager') or not self._meta_manager:
			if DEBUG_LOGGING:
				self._control_surface.log_message("[FLUSH_SKIP] No meta manager available")
			return 0

		if not self._meta_manager._pending_renames:
			#if DEBUG_LOGGING:
			#	self._control_surface.log_message("[FLUSH_NONE] No pending renames to process")
			return 0

		processed_count = 0
		skipped_invalid = 0

		# Process from end to beginning so we can safely delete items
		for i in reversed(list(range(len(self._meta_manager._pending_renames)))):
			pending_op = self._meta_manager._pending_renames[i]
			clip_id, target_name, clip_object_id = pending_op

			# Retrieve clip reference
			clip_obj = self._meta_manager._get_clip_by_object_id(clip_object_id)

			if not clip_obj or not hasattr(clip_obj, 'name'):
				# Clip was deleted or became invalid - remove from queue
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
					# Name changed but doesn't match exactly - partial success
					del self._meta_manager._pending_renames[i]
					processed_count += 1

			except RuntimeError:
				# Still blocked - leave in queue for next opportunity
				pass

		# Report summary
		remaining = len(self._meta_manager._pending_renames)

		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[FLUSH_COMPLETE] Success:{processed_count} Invalid:{skipped_invalid} Remaining:{remaining}"
			)

		return processed_count


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


	def _setup_observer_listeners(self):
		"""Register for Live structural change notifications."""
		try:
			song = self.song()

			# Track structure changes (add/remove/reorder tracks)
			song.add_tracks_listener(self._on_tracks_changed)

			# Scene structure changes (affect clip slot indexing)
			song.add_scenes_listener(self._on_scenes_changed)

			if DEBUG_LOGGING:
				self._control_surface.log_message("[OBSERVERS] Registered track/scene listeners")

		except Exception as e:
			self._control_surface.log_message(f"[OBSERVER_REGISTRATION_ERROR] {e}")

	def _remove_observer_listeners(self):
		"""Unregister observers during disconnect to prevent memory leaks."""
		try:
			song = self.song()
			song.remove_tracks_listener(self._on_tracks_changed)
			song.remove_scenes_listener(self._on_scenes_changed)

			if DEBUG_LOGGING:
				self._control_surface.log_message("[OBSERVERS] Unregistered listeners")

		except:
			pass  # Ignore errors during shutdown

	def _on_tracks_changed(self):
		"""Called when tracks are added/removed/reordered."""
		# Guard clause - skip if meta manager unavailable
		if not hasattr(self, '_meta_manager') or not self._meta_manager:
			if DEBUG_LOGGING:
				self._control_surface.log_message("[TRACKS_CHANGED] Skipping (no meta manager)")
			return

		try:
			self._meta_manager.synchronize_all_clip_locations()
			if DEBUG_LOGGING:
				self._control_surface.log_message("[TRACKS_CHANGED] Full sync triggered")
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._control_surface.log_message(f"[SYNC_ERROR] {e}\n{traceback.format_exc()}")

	def _on_scenes_changed(self):
		"""Called when scenes are added/removed/reordered."""
		if not hasattr(self, '_meta_manager') or not self._meta_manager:
			if DEBUG_LOGGING:
				self._control_surface.log_message("[SCENES_CHANGED] Skipping (no meta manager)")
			return

		try:
			self._meta_manager.synchronize_all_clip_locations()
			if DEBUG_LOGGING:
				self._control_surface.log_message("[SCENES_CHANGED] Full sync triggered")
		except Exception as e:
			if DEBUG_LOGGING:
				import traceback
				self._control_surface.log_message(f"[SYNC_ERROR] {e}\n{traceback.format_exc()}")

	def set_clip(self, clip):
		"""Set current clip and load/restore its settings."""
		# Normalize input (could be ClipSlot or Clip)
		clip = self._normalize_clip(clip)

		if clip is None:
			# Save previous clip state before clearing
			if self._meta_manager and self._clip:
				try:
					old_state = self._get_current_state_dict()
					self._meta_manager.save_clip_to_json(self._clip, old_state)
				except Exception as e:
					if DEBUG_LOGGING:
						self._control_surface.log_message(f"[PREV_CLIP_SAVE_ERROR] {e}")

			if self._clip:
				self._init_data()
			self._clip = None
			self._force_update = True
			self.update()
			return

		# Skip if same clip already selected
		if self._clip == clip:
			return

		# === PHASE 1: TRY LOAD EMBEDDED PARAMETER TAG FROM CLIP NAME FIRST ===
		clip_params = None
		settings_source = None

		clip_params = self.extract_embedded_parameters(getattr(clip, 'name', ''))

		if clip_params:
			settings_source = "EMBEDDED_TAG"
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[CLIP_NAME_HAS_SUX_TAG] Using embedded params")
		else:
			# === PHASE 2: FALLBACK TO JSON RECOVERY WITH VERIFICATION ===
			if hasattr(self._meta_manager, 'recover_clip_settings_from_json'):
				json_result = self._meta_manager.recover_clip_settings_from_json(clip)

				if json_result:
					if isinstance(json_result, dict) and json_result.get("requires_verification"):
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								f"[USER_ACTION_NEEDED] Settings recovered but need verification."
							)
						clip_params = json_result.get("settings")
						settings_source = "JSON_UNVERIFIED"
					else:
						clip_params = json_result
						settings_source = "JSON_MATCHED"

			if not clip_params:
				# Completely new clip - use defaults
				clip_params = self._get_current_state_dict()
				settings_source = "DEFAULTS"

		# === PHASE 3: SAVE PREVIOUS CLIP STATE TO JSON ===
		if self._meta_manager and self._clip:
			try:
				old_state = self._get_current_state_dict()
				self._meta_manager.save_clip_to_json(self._clip, old_state)
			except Exception as e:
				if DEBUG_LOGGING:
					self._control_surface.log_message(f"[PREV_STATE_SAVE_ERROR] {e}")

		# === PHASE 4: INITIALIZE NEW CLIP AND APPLY SETTINGS ===
		self._clip = clip
		self._init_data()

		if clip_params:
			try:
				self.load_clip_settings(clip, clip_params)
				if DEBUG_LOGGING:
					self._control_surface.log_message(f"[RESTORED_FROM] {settings_source}")
			except Exception as e:
				if DEBUG_LOGGING:
					self._control_surface.log_message(f"[LOAD_SETTINGS_ERROR] {e}")
		else:
			if DEBUG_LOGGING:
				self._control_surface.log_message("[DEFAULTS] Using factory defaults for new clip")

		# === CRITICAL ADDITION: WRITE TAG TO CLIP NAME AFTER LOADING ===
		# Even if loaded from JSON, ensure it's now embedded in the name
		if self._meta_manager:
			try:
				current_state = self._get_current_state_dict()
				added_track_slot_info = self._meta_manager._get_current_track_slot_indices_safe(clip) if hasattr(
					self._meta_manager, '_get_current_track_track_indices_safe') else (-1, -1)
				current_state['track_index'] = added_track_slot_info[0]
				current_state['slot_index'] = added_track_slot_info[1]

				success = self.update_clip_name_with_params(clip, current_state)

				# DO NOT LOG "WRITTEN" HERE - let update_clip_name_with_params handle its own logging
				# That method knows whether it was immediate or queued
				if DEBUG_LOGGING and success:
					self._control_surface.log_message(
						f"[TAG_ENQUEUE] Initiated tag write operation (check logs above for result)"
					)

			except Exception as e:
				if DEBUG_LOGGING:
					import traceback
					self._control_surface.log_message(f"[TAG_WRITE_ERROR] {e}\n{traceback.format_exc()}")

		# Mark initialization complete
		self._initializing = False

		# Register listeners and force UI refresh
		self._register_clip_slot_listener()
		self._force_update = True
		self.update()

		# Final debug logging
		cname = getattr(clip, 'name', '(unnamed)').split(' [')[0][:30] if clip and clip.name else "(unnamed)"
		tidx, sidx = self._meta_manager._get_current_track_slot_indices_safe(clip) if self._meta_manager else (-1, -1)

		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[CLIP_SET] '{cname}' @ T{tidx}:S{sidx} (source={settings_source})"
			)

	# Add at the end of MelodicNoteEditorComponent class, near other utility methods:

	def manually_write_all_tags(self):
		"""
        MANUAL DEBUGGING TOOL: Forces all current clip parameters to be written
        immediately as tags. Useful for verifying tags persist across restarts.

        Call from Python console:
            from your_module import melodic_sequencer_instance
            melodic_sequencer_instance.manually_write_all_tags()
        """
		if not self._clip:
			self._control_surface.show_message("No clip selected")
			return

		try:
			current_state = self._get_current_state_dict()

			# Try direct write
			success = self.update_clip_name_with_params(self._clip, current_state)

			if success:
				# Verify it worked
				clip_name = self._clip.name
				if "[SUX:" in clip_name:
					self._control_surface.show_message("Tag wrote successfully!")
					self._control_surface.log_message(f"[MANUAL_DEBUG] Tag present: {clip_name[:60]}...")
				else:
					self._control_surface.show_message("Tag attempted but not verified")
					self._control_surface.log_message(f"[MANUAL_DEBUG] Warning: Tag may not have taken effect")
			else:
				self._control_surface.show_message("Tag write failed")

		except Exception as e:
			import traceback
			self._control_surface.show_message("Error writing tag")
			self._control_surface.log_message(f"[MANUAL_DEBUG ERROR] {e}\n{traceback.format_exc()}")

	def _get_simple_clip_key(self, clip):
		"""Fallback: Simple key using object identity when get_key() fails."""
		if not clip:
			return None
		clip_name = getattr(clip, 'name', 'Unknown')
		return f"FALLBACK_{id(clip)}::{clip_name}"

	def _get_current_state_dict(self):
		"""Returns current settings as dictionary WITHOUT modifying them."""
		scale_val = 0
		root_val = 0
		resolution_index = 0

		try:
			if hasattr(self._step_sequencer, '_scale_selector') and self._step_sequencer._scale_selector:
				selector = self._step_sequencer._scale_selector
				scale_val = getattr(selector, '_modus', 0)
				root_val = getattr(selector, '_key', 0)

			# === RESOLUTION HANDLING USING RESOLUTION_MAP ===
			current_resolution = getattr(self, '_resolution', 16)

			from .SequencerConstants import RESOLUTION_MAP

			if isinstance(current_resolution, float):
				resolution_index = None
				for i, map_value in enumerate(RESOLUTION_MAP):
					if abs(map_value - current_resolution) < 0.0001:
						resolution_index = i
						break

				if resolution_index is None:
					resolution_index = len(RESOLUTION_MAP) // 2

				res_label = RESOLUTION_NAMES[resolution_index] if resolution_index < len(
					RESOLUTION_NAMES) else "UNKNOWN"
			else:
				resolution_index = int(current_resolution)
				if resolution_index >= len(RESOLUTION_MAP):
					resolution_index = len(RESOLUTION_MAP) - 1
				if resolution_index < 0:
					resolution_index = 0
				res_label = RESOLUTION_NAMES[resolution_index]

			display_octave = getattr(self, '_display_octave', 2)

			loop_block = 0
			loop_page_offset = 0
			clip_loop_start = 0.0
			clip_loop_end = 0.0

			if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
				ls = self._step_sequencer._loop_selector
				loop_block = getattr(ls, '_block', 0)
				loop_page_offset = getattr(ls, '_loop_page_offset', 0)

				if self._clip:
					clip_loop_start = round(getattr(self._clip, 'loop_start', 0.0), 4)
					clip_loop_end = round(getattr(self._clip, 'loop_end', 0.0), 4)

		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[STATE_DICT ERROR] {e}")
			return {
				'scale': 0,
				'root_note': 0,
				'display_octave': 2,
				'resolution_index': 4,
				'resolution_name': "1/16",
				'loop_block': 0,
				'loop_page_offset': 0,
				'clip_loop_start': 0.0,
				'clip_loop_end': 0.0,
				'timestamp': time.time()
			}

		result = {
			'scale': scale_val,
			'root_note': root_val,
			'display_octave': int(display_octave),
			'resolution_index': resolution_index,  # ← THIS LINE MUST ALWAYS BE PRESENT
			'resolution_name': res_label,  # ← Human-readable label for reference
			'loop_block': loop_block,
			'loop_page_offset': loop_page_offset,
			'clip_loop_start': clip_loop_start,
			'clip_loop_end': clip_loop_end,
			'timestamp': time.time()
		}

		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[SAVING_STATE] Scale={scale_val}, Root={root_val}, Oct={display_octave}, " +
				f"ResIdx={resolution_index} ({res_label}), Offset={loop_page_offset}, " +
				f"Loop={clip_loop_start:.2f}->{clip_loop_end:.2f}"
			)

		return result



	def _restore_clip_metadata(self, metadata):
		"""Applies saved metadata to the current session."""
		if not metadata: return
		try:
			# === SCALE/ROOT/OCTAVE/RESOLUTION ===
			if 'scale' in metadata and hasattr(self._step_sequencer, '_scale_selector'):
				selector = self._step_sequencer._scale_selector
				if metadata['scale'] in range(len(selector._modus_names)):
					selector.set_modus(metadata['scale'], False, True)

			if 'root_note' in metadata and hasattr(self._step_sequencer, '_scale_selector'):
				selector = self._step_sequencer._scale_selector
				root = int(metadata['root_note']) % 12
				selector.set_key(root, False, True)

			if 'display_octave' in metadata:
				oct_val = int(metadata['display_octave'])
				oct_val = max(0, min(15, oct_val))
				self.set_display_octave(oct_val)

			if 'resolution' in metadata:
				res = int(metadata['resolution'])
				if res in [8, 16, 32]:
					self.set_resolution(res)

			# === NEW: RESTORE LOOP PARAMETERS ==========
			if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
				ls = self._step_sequencer._loop_selector

				# Restore selected block/index (row 7 selection)
				if 'loop_block' in metadata:
					new_block = int(metadata['loop_block'])
					if hasattr(ls, '_block'):
						ls._block = new_block
						# Force update to reflect new selection visually
						ls._force = True
						if DEBUG_LOGGING:
							self._control_surface.log_message(f"[RESTORE_LOOP] Block={new_block}")

				# Restore page offset (cycle/page position)
				if 'loop_page_offset' in metadata:
					offset = int(metadata['loop_page_offset'])
					if hasattr(ls, '_loop_page_offset'):
						old_offset = ls._loop_page_offset
						ls._loop_page_offset = offset
						# Update step sequencer page accordingly
						if hasattr(self._step_sequencer, 'set_page'):
							absolute_block = ls._block + (offset * 8)
							self._step_sequencer.set_page(absolute_block)
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								f"[RESTORE_LOOP] PageOffset={offset} (was {old_offset})")

				# Restore clip loop bounds (marker start/end)
				if 'clip_loop_start' in metadata and 'clip_loop_end' in metadata:
					if self._clip:
						start = float(metadata['clip_loop_start'])
						end = float(metadata['clip_loop_end'])

						# Safety check
						if end <= start:
							end = start + 1.0

						# Apply to clip directly
						try:
							self._clip.loop_start = start
							self._clip.loop_end = end
							# Also update marker positions
							self._clip.start_marker = start
							self._clip.end_marker = end

							# Tell LoopSelector to sync
							if hasattr(ls, '_get_clip_loop'):
								ls._get_clip_loop()
								ls.update()

							if DEBUG_LOGGING:
								self._control_surface.log_message(f"[RESTORE_LOOP] ClipLoop: {start:.2f} -> {end:.2f}")

						except RuntimeError as le:
							if DEBUG_LOGGING:
								self._control_surface.log_message(f"[RESTORE_ERROR] Failed to set clip loop: {le}")

			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[RESTORE] Octave={metadata.get('display_octave')}, Res={metadata.get('resolution')}, " +
					f"Block={metadata.get('loop_block')} Offset={metadata.get('loop_page_offset')}")

		except Exception as e:
			self._control_surface.log_message(f"[RESTORE ERROR] {e}")

		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[RESTORE_VERIFICATION] Octave={metadata.get('display_octave')}, Res={metadata.get('resolution')}, " +
				f"Block={metadata.get('loop_block', '?')} Offset={metadata.get('loop_page_offset', '?')}"
			)

	def load_clip_settings(self, clip, settings_dict):
		"""Loads settings from dictionary into active clip's UI state."""
		if not settings_dict:
			if DEBUG_LOGGING:
				self._control_surface.log_message("[LOAD_SETTINGS] No settings provided, using current defaults")
			return

		try:
			# === SCALE/ROOT ===
			if 'scale' in settings_dict and hasattr(self._step_sequencer, '_scale_selector'):
				selector = self._step_sequencer._scale_selector
				mode_idx = int(settings_dict.get('scale', 0))
				if mode_idx < len(selector._modus_names):
					selector.set_modus(mode_idx, False, True)

			if 'root_note' in settings_dict and hasattr(self._step_sequencer, '_scale_selector'):
				selector = self._step_sequencer._scale_selector
				root_note = int(settings_dict['root_note']) % 12
				selector.set_key(root_note, False, True)

			# === OCTAVE ===
			if 'display_octave' in settings_dict:
				oct_val = int(settings_dict['display_octave'])
				oct_val = max(0, min(15, oct_val))
				old_oct = getattr(self, '_display_octave', 2)
				if oct_val != old_oct:
					self._display_octave = oct_val
					self._parse_notes()

			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[LOADING_DISPLAY_OCTAVE] From JSON={settings_dict.get('display_octave')} " +
					f"To Internal={getattr(self, '_display_octave', '?')}"
				)

			# === RESOLUTION: CRITICAL FIX WITH VALIDATION ===
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

				# CONVERT INDEX → BEAT VALUE
				new_resolution_beats = float(RESOLUTION_MAP[res_idx])

				if DEBUG_LOGGING:
					from .SequencerConstants import RESOLUTION_NAMES
					res_name = RESOLUTION_NAMES[res_idx] if 0 <= res_idx < len(RESOLUTION_NAMES) else "UNKNOWN"
					self._control_surface.log_message(
						f"[LOADING_RESOLUTION] JSON idx={res_idx} ({res_name}) → Converted to {new_resolution_beats:.2f} beats"
					)

			# Backward compatibility: Support legacy 'resolution' field containing beat values
			elif 'resolution' in settings_dict:
				res_val = int(settings_dict['resolution'])

				# ★★★ VALIDATION: Reject zero/negative resolutions ★★
				if res_val <= 0:
					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[RESOLUTION_INVALID] Legacy value {res_val} rejected (must be positive)"
						)
					# Fall back to safe default
					new_resolution_beats = RESOLUTION_MAP[4]  # Default 1/16th note
				else:
					# If it's already a beat-like number (8, 16, 32, etc.), use directly
					new_resolution_beats = float(res_val)

					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[LOADING_RESOLUTION_LEGACY] BeatVal={res_val} → {new_resolution_beats:.2f} beats"
						)
			else:
				# No resolution field found - use safe default
				new_resolution_beats = RESOLUTION_MAP[4]  # Default 1/16th note
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[RESOLUTION_MISSING] Using default {new_resolution_beats:.2f} beats"
					)

			# Apply the converted beat value ONLY if we got a valid conversion
			# Safety net: ensure resolution_never exceeds zero
			if new_resolution_beats is not None and new_resolution_beats > 0:
				old_res_beats = getattr(self, '_resolution', 0.25)
				self._resolution = new_resolution_beats  # ← ASSIGN TO _resolution, NOT resolution_beats
				self._parse_notes()

				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[RESOLUTION_APPLIED] Old={old_res_beats:.2f} New={new_resolution_beats:.2f}"
					)
			else:
				# Fallback: Use minimum safe resolution
				min_resolution = min(RESOLUTION_MAP)
				old_res_beats = getattr(self, '_resolution', 0.25)
				self._resolution = min_resolution  # ← ASSIGN TO _resolution, NOT resolution_beats

				if DEBUG_LOGGING:
					self._control_surface.log_message(
						f"[RESOLUTION_FALLBACK] Unsafe value detected → Set to minimum {min_resolution:.2f} beats"
					)
					self._parse_notes()

			# === LOOP PARAMETERS (LoopSelector) ===
			if hasattr(self._step_sequencer, '_loop_selector') and self._step_sequencer._loop_selector:
				ls = self._step_sequencer._loop_selector

				if 'loop_block' in settings_dict:
					block = int(settings_dict['loop_block'])
					if hasattr(ls, '_block'):
						ls._block = block
						ls._force = True

				if 'loop_page_offset' in settings_dict:
					offset = int(settings_dict['loop_page_offset'])

					if hasattr(ls, '_loop_page_offset'):
						ls._last_known_offset = offset - 1
						ls._loop_page_offset = offset

					if hasattr(self._step_sequencer, '_loop_page_offset'):
						self._step_sequencer._loop_page_offset = offset

					absolute_block = ls._block + (offset * 8)
					if hasattr(self._step_sequencer, 'set_page'):
						self._step_sequencer.set_page(absolute_block)

					if DEBUG_LOGGING:
						self._control_surface.log_message(
							f"[LOOP_PAGE] Offset={offset}, Block={ls._block}, AbsolutePage={absolute_block}"
						)


				if 'clip_loop_start' in settings_dict and 'clip_loop_end' in settings_dict:
					if self._clip:
						start = float(settings_dict['clip_loop_start'])
						end = float(settings_dict['clip_loop_end'])
						if end > start:
							try:
								self._clip.loop_start = start
								self._clip.loop_end = end
								self._clip.start_marker = start
								self._clip.end_marker = end
								if hasattr(ls, '_get_clip_loop'):
									ls._get_clip_loop()
									ls.update()
							except RuntimeError:
								pass

				# Refresh cycle button visual state now that loop params are synced
				if hasattr(self._step_sequencer, '_update_cycle_button'):
					try:
						self._step_sequencer._update_cycle_button()
						if DEBUG_LOGGING:
							self._control_surface.log_message(
								"[CYCLE_BUTTON_REFRESH] Post-loop-parameter-update forced refresh"
							)
					except Exception as e:
						if DEBUG_LOGGING:
							self._control_surface.log_message(f"[CYCLE_BUTTON_ERROR] {e}")


			if DEBUG_LOGGING:
				self._control_surface.log_message(
					f"[SETTINGS_APPLIED] Oct={settings_dict.get('display_octave')} ResIdxOrBeats={settings_dict.get('resolution_index', settings_dict.get('resolution'))} Scale={settings_dict.get('scale')}"
				)

		except Exception as e:
			import traceback
			self._control_surface.log_message(f"[SET_LOADING ERROR] {e}\n{traceback.format_exc()}")

		# === VERIFICATION CHECKLIST ===
		if DEBUG_LOGGING:
			final_res = getattr(self, '_resolution', 'NOT SET')
			final_offset = getattr(self._step_sequencer._loop_selector, '_loop_page_offset', 'NOT SET') if hasattr(
				self._step_sequencer, '_loop_selector') else 'NO LS'
			final_block = getattr(self._step_sequencer._loop_selector, '_block', 'NOT SET') if hasattr(
				self._step_sequencer, '_loop_selector') else 'NO LS'

			self._control_surface.log_message(
				f"[VERIFICATION_CHECK] Resolution_Beats={final_res:.2f} PageOffset={final_offset} Block={final_block}"
			)

			# Compare saved vs loaded values
			saved_res_idx = settings_dict.get('resolution_index', 'N/A')
			expected_beats = None
			if saved_res_idx != 'N/A':
				from .SequencerConstants import RESOLUTION_MAP
				try:
					expected_beats = float(RESOLUTION_MAP[int(saved_res_idx)])
				except (IndexError, ValueError):
					expected_beats = None

			if expected_beats is not None and abs(float(final_res) - expected_beats) > 0.001:
				self._control_surface.log_message(
					f"[WARNING_RES_MISMATCH] Expected Beats={expected_beats:.2f} Got={final_res:.2f}"
				)

		# Force refresh
		self._force_update = True
		self.update()

		if DEBUG_LOGGING:
			self._control_surface.log_message(
				f"[FINAL_PUSH] ForceUpdate=True UpdateCalled=True"
			)

	# --- HELPER METHODS FOR METADATA MANAGER ---

	def _load_metadata_cache(self):
		"""Loads the JSON file into memory."""
		if not self._metadata_file_path or not os.path.exists(self._metadata_file_path):
			return

		try:
			with open(self._metadata_file_path, 'r') as f:
				self._metadata_cache = json.load(f)
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[META] Loaded {len(self._metadata_cache)} clip entries")
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
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[FALLBACK_KEY] Using fallback key: {primary_key}")

		if primary_key:
			# Validate loop data before saving
			if 'clip_loop_start' in data_dict and data_dict[
				'clip_loop_start'] == 0.0 and 'clip_loop_end' in data_dict and data_dict['clip_loop_end'] == 0.0:
				if DEBUG_LOGGING:
					self._control_surface.log_message(
						"[WARN] Saving zero-length loop - may indicate uninitialized clip")

			self._meta_manager.cache[primary_key] = data_dict
			self._meta_manager._save_cache()
			if DEBUG_LOGGING:
				file_size = self._meta_manager.path.stat().st_size if self._meta_manager.path.exists() else 0
				self._control_surface.log_message(
					f"[CACHE_WRITE] Wrote entry under key '{primary_key}', file now {file_size} bytes")

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

		if orphans:
			self._meta_manager._save_cache()
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[CLEANUP] Removed {len(orphans)} orphan entries: {orphans}")


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
			if DEBUG_LOGGING:
				self._control_surface.log_message("[ENABLED] Refreshing button state")
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
		old_mode = self._mode
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
			if mode == STEPSEQ_MODE_OCTAVE_OVERVIEW:
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

				if DEBUG_LOGGING:
					self._control_surface.log_message(f"[ANIM->EDIT] Killed anim, disabled LS for {mode}")

		# --- DEBUG: COPY/PASTE STATE MANAGEMENT ---
		self._control_surface.log_message("[SET MODE] Entering Mode=%s | OldMode=%s" % (mode, old_mode))

		if mode == STEPSEQ_MODE_NOTES:
			if DEBUG_LOGGING:
				self._control_surface.log_message(
				"[SET MODE] In NOTES block. Armed=%s | Data=%s" %
				(str(self._paste_armed), str(len(self._copied_grid_data) if self._copied_grid_data else 0))
				)
			# Force LED update
			self._update_copy_paste_button_state()

		elif mode != STEPSEQ_MODE_NOTES:
			if DEBUG_LOGGING:
				self._control_surface.log_message("[SET MODE] Leaving NOTES block.")
			# Leaving Notes mode (e.g., going to Velocity Editor). To clear buffer on exit, uncomment below:
			# self._copied_grid_data = None
			# self._paste_armed = False
			pass

		# DEBUG LOGGING FOR ROW 7 OWNERSHIP
		# if DEBUG_LOGGING:
		# 	self._control_surface.log_message(
		# 	"[set_mode] Row 7 Ownership: %s (mode=%s)" %
		# 	("EDITOR" if self.uses_bottom_row() else "LOOP_SELECTOR", mode)
		# )

		if DEBUG_LOGGING:
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
							if DEBUG_LOGGING:
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

	def set_resolution(self, resolution):
		"""Set resolution with validation BEFORE storage."""

		# === VALIDATION PHASE (before any storage) ===
		if resolution <= 0:
			if DEBUG_LOGGING:
				self._control_surface.log_message("[WARN] Invalid resolution set to default")
			resolution = 16  # Default fallback

		# Store raw value (caller's responsibility)
		old_resolution = self._resolution if hasattr(self, '_resolution') else 0.25
		self._resolution = resolution

		if DEBUG_LOGGING:
			old_beats = self.resolution_beats  # Use safe accessor to display what was actually used
			new_idx = None
			from .SequencerConstants import RESOLUTION_MAP
			try:
				# Find which index this corresponds to
				for i, v in enumerate(RESOLUTION_MAP):
					if abs(v - resolution) < 0.0001:
						new_idx = i
						break
			except:
				pass
			self._control_surface.log_message(
				f"[SET_RESOLUTION] Raw={resolution} → Safe Beats={old_beats:.2f}" +
				(f" → Index {new_idx}" if new_idx is not None else "")
			)

		# Update internal buffers if clip exists
		if self._clip is not None:
			self._parse_notes()
			self._update_matrix()

	# def set_quantization(self, quantization):
	#
	# 	old_quantize = self._quantization
	# 	self._quantization = quantization
	#
	# 	# update loop point
	# 	if self._clip != None and old_quantize != self._quantization:
	#
	# 		self._loop_start = int(
	# 			self._clip.loop_start *
	# 			self._quantization /
	# 			old_quantize
	# 		)
	#
	# 		self._loop_end = int(
	# 			self._clip.loop_end *
	# 			self._quantization /
	# 			old_quantize
	# 		)
	#
	# 		# safety
	# 		if self._loop_end <= self._loop_start:
	# 			self._loop_end = self._loop_start + 1
	#
	# 		try:
	# 			self._clip.loop_start = self._loop_start
	# 			self._clip.loop_end = self._loop_end
	#
	# 			self._clip.start_marker = self._loop_start
	# 			self._clip.end_marker = self._loop_end
	#
	# 		except RuntimeError:
	# 			pass
	#
	# 		# IMPORTANT:
	# 		# do not rewrite notes during controller init
	# 		if not self._initializing:
	# 			self._update_clip_notes()

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
				for j in range(min(7, len(self._key_indexes))):
					display_pitch = (self._key_indexes[j] + 12 * (self._display_octave - 2))
					if note_key == display_pitch:
						# Another safety check for pitches array
						pitch_idx = i * 7 + j
						if pitch_idx < len(self._notes_pitches):
							self._notes_pitches[pitch_idx] = 1

		except Exception as e:
			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[PARSE NOTES ERROR] {e}")

	def _toggle_note_at_grid_position(self, idx, y):
		grid_time = idx * self.resolution_beats
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
				self.resolution_beats,
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
						note_time = x * self.resolution_beats
						#time = x * self._quantization
						velocity = self._velocity_map[self._notes_velocities[x]]
						length = self._length_map[self._notes_lengths[x]] * self.resolution_beats / 4.0
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
		# if DEBUG_LOGGING:
		# 	self._control_surface.log_message("UPDATE_MATRIX mode=%d" % self._mode)

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
					# if DEBUG_LOGGING:
					# 	self._control_surface.log_message(f"[UPDATE_MATRIX] ROOT_KEY={root_key}")
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
						# if DEBUG_LOGGING:
						# 	self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
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
					# self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
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
						# self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
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
					# self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, "DefaultButton.Disabled"))
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
						idx = self._get_step_index(x)
						if self._mode == STEPSEQ_MODE_NOTES:
							# --- SCALE CONFIGURATION ---
							scale_root_key = 0
							scale_notes = [0, 2, 4, 5, 7, 9, 11]

							if hasattr(self, "_step_sequencer") and self._step_sequencer and \
									hasattr(self._step_sequencer, "_scale_selector") and \
									self._step_sequencer._scale_selector:
								selector = self._step_sequencer._scale_selector
								scale_root_key = selector._key
								scale_notes = [n % 12 for n in selector.notes]

							#idx = self._get_step_index(x) #already above
							start_time = idx * self.resolution_beats
							end_time = start_time + self.resolution_beats
							step_notes_list = self._get_notes_at_step(idx)

							for y in range(7):
								row_idx = 6 - y
								current_pitch = (self._key_indexes[row_idx] + 12 * (self._display_octave - 2))

								is_midi_valid = (0 <= current_pitch <= 127)
								target_color = "StepSequencer2.Pitch.Off"

								# --- CHECKERBOARD FOR INVALID PITCHES ---
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

										# 2. Row Mapping Check
										mapped_row = self._get_row_for_pitch(note_midi)
										if mapped_row != row_idx:
											continue

										# 3. STRICT OCTAVE FILTER
										# Ensure the note's octave matches the row's octave
										note_octave = int(note_midi / 12)
										expected_octave = int(current_pitch / 12)

										if note_octave != expected_octave:
											continue

										notes_mapped_to_this_row.append(n)

									has_note_here = len(notes_mapped_to_this_row) > 0

									if not has_note_here:
										# No note here: Draw Scale Colors
										interval = (current_pitch - scale_root_key) % 12
										is_root = (interval == 0)

										if is_root:
											target_color = "StepSequencer2.Pitch.RootNote"
										# elif is_chord_tone:
										#	target_colour = "StepSequencer2.Pitch.ChordTone"
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

						# elif self._mode == STEPSEQ_MODE_NOTES_OCTAVES:
						# 	# OCTAVE MODE LOGIC
						# 	for y in range(7):
						# 		has_note_in_row = (self._notes_pitches[(idx * 7) + (6 - y)] == 1)
						#
						# 		if has_note_in_row:
						# 			if self._notes_octaves[idx] == (6 - y):
						# 				color_octave = "StepSequencer2.Octave.On"
						# 			else:
						# 				color_octave = "StepSequencer2.Octave.Off"
						# 		else:
						# 			if self._notes_octaves[idx] == (6 - y):
						# 				color_octave = "StepSequencer2.Octave.Dim"
						# 			else:
						# 				color_octave = "StepSequencer2.Octave.Off"
						#
						# 		#self._control_surface.log_message("WRITE (%d,%d) <- %s" % (x, y, color_octave))
						# 		self._grid_back_buffer[x][y] = color_octave


						elif self._mode == STEPSEQ_MODE_OCTAVE_OVERVIEW:
							start_octave = self._overview_start_octave
							max_visible_octave = start_octave + 7
							min_visible_octave = start_octave

							# Pre-scan notes for this step
							highest_note_octave = -1
							lowest_note_octave = 128
							step_notes = self._get_notes_at_step(self._get_step_index(x))

							for n in step_notes:
								octave = int(n[0] / 12)
								if octave > highest_note_octave: highest_note_octave = octave
								if octave < lowest_note_octave: lowest_note_octave = octave

							for y in range(8):
								target_octave = start_octave + (7 - y)
								has_note_here = False

								# Check if note exists in this exact octave
								for n in step_notes:
									if int(n[0] / 12) == target_octave:
										has_note_here = True
										break

								target_color = "StepSequencer2.Octave.Off" # Default

								# --- PRIORITY 1: OVERFLOW INDICATORS (Edge Rows) ---
								# These always take precedence
								is_top_edge = (y == 0)
								is_bottom_edge = (y == 7)
								has_above_overflow = highest_note_octave > max_visible_octave
								has_below_overflow = lowest_note_octave < min_visible_octave

								if is_top_edge and has_above_overflow:
									diff = highest_note_octave - max_visible_octave
									if diff >= 3: target_color = "StepSequencer2.Octave.OnAbove3"
									elif diff == 2: target_color = "StepSequencer2.Octave.OnAbove2"
									elif diff == 1: target_color = "StepSequencer2.Octave.OnAbove1"

								elif is_bottom_edge and has_below_overflow:
									diff = min_visible_octave - lowest_note_octave
									if diff >= 3: target_color = "StepSequencer2.Octave.OnBelow3"
									elif diff == 2: target_color = "StepSequencer2.Octave.OnBelow2"
									elif diff == 1: target_color = "StepSequencer2.Octave.OnBelow1"

								# --- PRIORITY 2: LAST DISPLAYED OCTAVE (Inside Range) ---
								# Only applies if we are NOT using this row for overflow indication
								elif target_octave == self._last_displayed_octave + 1: # need the +1 because otherwise we shoo one row lower
									if has_note_here:
										target_color = "StepSequencer2.Octave.OnDisplay"
									else:
										target_color = "StepSequencer2.Octave.OffDisplay"

								# --- PRIORITY 3: NORMAL VISUALS ---
								elif has_note_here:
									target_color = "StepSequencer2.Octave.On"

								# Else: remains "StepSequencer2.Octave.Off"

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
													if note_len >= v * self.resolution_beats / 4.0:
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
											if pitch_max_length >= v * self.resolution_beats / 4.0:
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
					# Get safe resolution (always ≥ 0.001 beats)
					try:
						if hasattr(self, 'resolution_beats') and callable(getattr(self, 'resolution_beats')):
							res = self.resolution_beats
						elif hasattr(self, '_resolution'):
							res = max(0.001, float(self._resolution))  # Clamp unsafe values
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
				allow_row7 = (self._mode in (STEPSEQ_MODE_VERTICAL_VELOCITY,STEPSEQ_MODE_VERTICAL_LENGTH, STEPSEQ_MODE_OCTAVE_OVERVIEW))

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

					# elif self._mode == STEPSEQ_MODE_NOTES_OCTAVES:
					# 	if self._is_notes_octaves_shifted:
					# 		if x < 4:
					# 			for x1 in range(start, end):
					# 				self._notes_octaves[x1] = 6 - y
					# 		else:
					# 			for x1 in range(start, end):
					# 				if y < 3 and self._notes_octaves[x1] < 6:
					# 					self._notes_octaves[x1] = self._notes_octaves[x1] + 1
					# 				if y > 3 and self._notes_octaves[x1] > 0:
					# 					self._notes_octaves[x1] = self._notes_octaves[x1] - 1
					# 	else:
					# 		self._notes_octaves[idx] = 6 - y

					# --- OCTAVE OVERVIEW MODE: Click selects octave and returns to Notes ---
					elif self._mode == STEPSEQ_MODE_OCTAVE_OVERVIEW:
						# Calculate the absolute octave based on the clicked row and the current start offset
						# Row 0 (Top) -> Start + 7
						# Row 7 (Bottom) -> Start
						selected_octave = self._overview_start_octave + (6 - y)

						self.set_display_octave(selected_octave)
						self._control_surface.show_message("Selected Octave %d" % (selected_octave - 1))

						# Return to Normal Notes mode
						self.set_mode(STEPSEQ_MODE_NOTES)
						self._force_update = True
						self.update()
						return



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

						start_time = step_idx * self.resolution_beats
						end_time = start_time + self.resolution_beats

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

						target_length = self._length_map[len_bucket] * self.resolution_beats / 4.0

						start_time = step_idx * self.resolution_beats
						end_time = start_time + self.resolution_beats

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
										if note_l >= v * self.resolution_beats / 4.0:
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

						target_length = self._length_map[len_bucket] * self.resolution_beats / 4.0

						start_time = step_idx * self.resolution_beats
						end_time = start_time + self.resolution_beats

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
		return (self._key_indexes[6 - y] + 12 * (self._display_octave - 2))

	def _get_step_index(self, x):
		return x + 8 * self._get_effective_page()

	def _set_velocity_for_pitch_at_step(self, idx, pitch, velocity):
		start_time = idx * self.resolution_beats
		end_time = start_time + self.resolution_beats

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

		clip_slot = self.song().view.highlighted_clip_slot

		if (self.is_enabled() and clip_slot is not None and clip_slot.has_clip):
			# CRITICAL: If we are waiting for Copy release, DO NOT toggle on Toggle release
			if hasattr(self, '_duplicate_pending') and self._duplicate_pending:
				return # Just ignore this release event.

			# Only act on Release for normal toggling
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

			# --- NEW LOGIC: DETECT TOGGLE PRESS WHILE COPY IS HELD ---
			elif value > 0:  # On Press of Toggle
				if hasattr(self, '_mode_copy_paste_button') and self._mode_copy_paste_button:
					if self._mode_copy_paste_button.is_pressed():
						# Both buttons held -> TRIGGER DUPLICATE IMMEDIATELY
						current_mode = self._mode

						if current_mode == STEPSEQ_MODE_NOTES:
							self._duplicate_clip_to_next_slot()

							# Set suppression for Toggle release
							if hasattr(self, '_step_sequencer') and self._step_sequencer:
								if not hasattr(self._step_sequencer, '_suppress_toggle_release'):
									self._step_sequencer._suppress_toggle_release = False
								self._step_sequencer._suppress_toggle_release = True

							# Prevent Copy release from doing anything
							self._skip_copy_release = True
							return

					# If Copy is NOT held, proceed normally (no change)


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
		self._display_octave = octave
		# debug
		if DEBUG_LOGGING:
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
			if ((value == 0) and (sender.is_momentary())):
				now = time.time()
				current_mode = self._mode

				if current_mode == STEPSEQ_MODE_OCTAVE_OVERVIEW:
					time_delta = now - self._last_notes_octaves_button_press

					# --- DOUBLE CLICK ---
					if time_delta < 0.3:
						# Clear the pending Up action
						self._scroll_pending_action = False

						# Execute Down immediately
						if self._overview_start_octave > 0:
							self._overview_start_octave -= 1
							msg = "Octave Range Down"
						else:
							msg = "Min Octave Reached"

						self._control_surface.show_message(
							msg + "  (LP's gid displays [%d , %d])" % (self._overview_start_octave - 2, self._overview_start_octave + 7 - 2))
						self._force_update = True
						self.update()

					# --- SINGLE CLICK ---
					else:
						# Set flag and schedule Up action
						self._scroll_pending_action = True
						self._control_surface.schedule_message(3, self._execute_octave_up_scroll)

					self._last_notes_octaves_button_press = now
					return

				# --- ENTRY LOGIC (NOTES -> OVERVIEW) ---
				elif current_mode == STEPSEQ_MODE_NOTES:
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

					# Set Mode
					self.set_mode(STEPSEQ_MODE_OCTAVE_OVERVIEW)

					# Show message with the calculated range
					self._control_surface.show_message("Octave Overview ") #% (self._overview_start_octave, self._overview_start_octave + 7)
					self._step_sequencer._update_OSD()
					self.update()
					return
				else:
					self.set_mode(STEPSEQ_MODE_NOTES)
					self._control_surface.show_message("Notes Mode")
					self._step_sequencer._update_OSD()
					self.update()
		else:
			if self._mode_notes_octaves_button:
				self._mode_notes_octaves_button.set_light("DefaultButton.Disabled")

	def _execute_octave_up_scroll(self):
		"""
		Called 0.3s after a single click.
	 Checks if a double-click cancelled it.
		"""
		# If we are not in overview mode, do nothing
		if self._mode != STEPSEQ_MODE_OCTAVE_OVERVIEW:
			self._scroll_pending_action = False
			return

		# If the flag was cleared (by a double-click), abort
		if not self._scroll_pending_action:
			return

		# Otherwise, execute Up
		if self._overview_start_octave + 8 < 11:
			self._overview_start_octave += 1
			msg = "Octave Range Up"
		else:
			msg = "Max Octave Reached"

		self._control_surface.show_message(
			msg + " (LP's gid displays [%d , %d])" % (self._overview_start_octave - 2, self._overview_start_octave + 8 - 2))

		self._force_update = True
		self.update()

		# Reset flag
		self._scroll_pending_action = False


# VELOCITIES
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
			#self._control_surface.log_message("[LED UPD] Button object missing!")
			return

		state_str = "OFF"
		if self._paste_armed and self._copied_grid_data:
			state_str = "ON"
			self._mode_copy_paste_button.set_light("StepSequencer2.CopyPaste.Copied")
		else:
			state_str = "DIM"
			self._mode_copy_paste_button.set_light("StepSequencer2.CopyPaste.Dim")

		if DEBUG_LOGGING:
			self._control_surface.log_message(
			"[LED UPD] Setting Light to %s | Armed=%s | DataCount=%s" %
			(state_str, str(self._paste_armed), str(len(self._copied_grid_data) if self._copied_grid_data else 0))
			)

	def _mode_button_copy_paste_value(self, value, sender):
		assert (self._mode_copy_paste_button != None)
		assert (value in range(128))

		if self.is_enabled() and self._clip != None:
			current_mode = self._mode
			now = time.time()

			# --- FIX 2: PROHIBIT DURING ANIMATIONS ---
			# If velocity or length animation is running, ignore copy/paste buttons
			if self._velocity_wait_animation or self._length_wait_animation:
				return

			# --- FIX 1 & SAFETY: MODE CHECK ---
			# If not in Notes mode, we do nothing EXCEPT potentially updating the LED.
			# We DO NOT clear the buffer here. The buffer persists for the next session in Notes mode.
			if current_mode != STEPSEQ_MODE_NOTES:
				# Update LED to show Dim (not armed) if not in Notes mode?
				# Or keep it On if data exists? Let's keep it On to remind user they have data.
				if self._paste_armed and self._copied_grid_data:
					# Keep it On
					pass
				else:
					self._update_copy_paste_button_state()

				# CRITICAL: Do NOT clear _copied_grid_data or _paste_armed here!
				# Just exit.
				return

			# --- FIX: Skip if triggered via Toggle Sequence (Scenario B) ---
			if self._skip_copy_release:
				self._skip_copy_release = False
				return

			# --- PRESS EVENT (value > 0) ---
			if value > 0:
				# Check if Toggle is already held
				is_toggle_held = False
				if hasattr(self, '_clip_toggle_button') and self._clip_toggle_button:
					if self._clip_toggle_button.is_pressed():
						is_toggle_held = True

				if is_toggle_held:
					# Enter "Pending" state
					self._duplicate_pending = True
					return

				# DOUBLE CLICK DETECTION LOGIC (On Second Press)
				if (now - self._last_press_time) < 0.25:
					self._delete_action_on_release = True
				else:
					self._delete_action_on_release = False

				self._last_press_time = now
				return

			# --- RELEASE EVENT (value == 0) ---

			# 1. Check Pending State (Duplicate Scenario A)
			if self._duplicate_pending:
				self._duplicate_pending = False
				self._duplicate_clip_to_next_slot()
				if hasattr(self, '_step_sequencer') and self._step_sequencer:
					if not hasattr(self._step_sequencer, '_suppress_toggle_release'):
						self._step_sequencer._suppress_toggle_release = False
					self._step_sequencer._suppress_toggle_release = True
				return

			# 2. Handle Delete (Double Click Detected on Press)
			if self._delete_action_on_release:
				self._delete_action_on_release = False
				# Clear buffer ONLY on successful delete (optional, but good practice to clear after delete)
				# Or keep it? Usually delete clears the view, so buffer might be stale?
				# Let's clear buffer on delete to avoid pasting deleted notes.
				self._paste_armed = False
				self._copied_grid_data = None
				self._update_copy_paste_button_state()

				if current_mode == STEPSEQ_MODE_NOTES:
					self._control_surface.show_message("Erase Page")
					self._delete_notes_in_current_page()
					self.update()
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
			# Else: Not in Notes mode (handled by global check above)

		else:
			if self._mode_copy_paste_button:
				self._mode_copy_paste_button.set_light("DefaultButton.Disabled")

	def _copy_notes_to_buffer(self):
		"""Copies ALL notes in current page AND current octave from note_cache."""
		self._parse_notes()  # Ensure cache is up to date

		page_start_step = self._page * 8
		page_end_step = (self._page + 1) * 8

		# CRITICAL: Use SAFE accessor that guarantees beat value ≥ 0.001
		resolution = self.resolution_beats  # ← CHANGED FROM self._resolution

		# Calculate target MIDI octave based on current display
		target_midi_octave = int((self._key_indexes[0] + 12 * (self._display_octave - 2)) / 12)

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

			# Check 2: Does note belong to current displayed octave?
			note_midi_octave = int(note_pitch / 12)
			if note_midi_octave != target_midi_octave:
				continue

			# Valid note: Store with RELATIVE time (for pasting to any page)
			relative_time = note_time - (page_start_step * resolution)
			buffer.append((note_pitch, relative_time, note_length, note_velocity, note_muted))

		self._copied_grid_data = buffer

	def _paste_notes_from_buffer(self):
		"""
		Pastes notes from buffer onto the CURRENT page and CURRENT displayed octave.
		It preserves the relative pattern (step offset) but shifts pitch and time to match current view.
		"""
		if not self._copied_grid_data:
			return

		page_start_step = self._page * 8
		current_page_offset_time = page_start_step * self.resolution_beats

		# 1. Determine the Target Pitch Range
		# We need the MIDI Octave of the CURRENT display to shift pitches correctly.
		target_midi_octave = int((self._key_indexes[0] + 12 * (self._display_octave - 2)) / 12)

		new_notes = []
		added_count = 0

		for n in self._copied_grid_data:
			orig_pitch = n[0]
			rel_time = n[1]  # Time is stored relative to the original page start (0.0 to 8.0 steps)
			orig_length = n[2]
			orig_velocity = n[3]
			orig_muted = n[4]

			# Calculate original MIDI octave of the note
			orig_midi_octave = int(orig_pitch / 12)

			# --- PITCH SHIFTING LOGIC ---
			# Calculate the distance (in semitones) between the original octave and target octave
			octave_shift = target_midi_octave - orig_midi_octave
			new_pitch = orig_pitch + (octave_shift * 12)

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
			return

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

	def _delete_notes_in_current_page(self):
		"""Deletes ALL notes in current page AND current octave (in-scale + out-of-scale)."""
		self._parse_notes()  # Ensure cache is up to date

		page_start_step = self._page * 8
		page_end_step = (self._page + 1) * 8
		resolution = self.resolution_beats

		# Calculate target MIDI octave
		target_midi_octave = int((self._key_indexes[0] + 12 * (self._display_octave - 2)) / 12)

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

			# Check 2: Does note belong to current displayed octave?
			note_midi_octave = int(note_pitch / 12)
			if note_midi_octave == target_midi_octave:
				# It matches current page AND current octave -> DELETE IT
				is_deleted = True
				deleted_count += 1
			else:
				# Different octave -> KEEP IT
				cleaned_notes.append(n)

		if deleted_count > 0:
			self._clip.select_all_notes()
			self._clip.replace_selected_notes(tuple(cleaned_notes))
			self._note_cache = tuple(cleaned_notes)
			self._parse_notes()
			self.update()
			self._control_surface.show_message("%d erased" % deleted_count)
		else:
			self._control_surface.show_message("Nothing to erase")

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

		# Prepare destination
		if next_clip_slot.has_clip:
			if next_clip_slot.is_playing:
				next_clip_slot.stop()
			next_clip_slot.delete_clip()

		dst_clip = None
		try:
			next_clip_slot.create_clip(src_clip.length)
			dst_clip = next_clip_slot.clip
		except RuntimeError:
			self._control_surface.show_message("Error creating clip")
			return

		if not dst_clip:
			self._control_surface.show_message("Failed to create clip")
			return

		# --- RETRIEVE AND CONVERT NOTES (Live 12 Format) ---
		note_tuples = []
		try:
			src_clip.select_all_notes()
			raw_notes = src_clip.get_selected_notes_extended()
			src_clip.deselect_all_notes()

			if raw_notes:
				for n in raw_notes:
					if hasattr(n, 'pitch'):
						pitch = int(n.pitch)
						time = float(n.start_time)
						length = float(n.duration)
						velocity = int(n.velocity)
						mute = 0
					elif isinstance(n, (list, tuple)) and len(n) >= 4:
						pitch, time, length, velocity = int(n[0]), float(n[1]), float(n[2]), int(n[3])
						mute = 0
					else:
						continue

					note_tuples.append((pitch, time, length, velocity, mute))
			else:
				self._control_surface.show_message("Source clip has no notes")
				return
		except Exception:
			self._control_surface.show_message("Error reading source notes")
			return

		if not note_tuples:
			return

		# --- APPLY NOTES ---
		try:
			dst_clip.select_all_notes()
			dst_clip.replace_selected_notes(tuple(note_tuples))

			# >>>>> NEW CODE STARTS HERE <<<<<
			# Highlight the new clip slot so the user sees it immediately
			# This works for both existing slots and newly created scenes
			if hasattr(song, 'view'):
				song.view.highlighted_clip_slot = next_clip_slot
			# >>>>> NEW CODE ENDS HERE <<<<<

			self._control_surface.show_message("Duplicated! (%d notes)" % len(note_tuples))

		except Exception as e:
			self._control_surface.log_message(f"[DUP] Application Error: {e}")
			self._control_surface.show_message("Duplication Failed")

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
							# Found it! Update JSON tracking
							if DEBUG_LOGGING:
								self._control_surface.log_message(
									f"[FOUND_MOVED_CLIP] Relocated to T{track_idx}:S{slot_idx}"
								)

							# Update cache entry with new position
							cached_entry["track_index"] = track_idx
							cached_entry["slot_index"] = slot_idx
							cached_entry["moved_count"] = cached_entry.get("moved_count", 0) + 1
							cached_entry["updated_at"] = time.time()

							self._meta_manager._save_cache()
							return True

			if DEBUG_LOGGING:
				self._control_surface.log_message(f"[NOT_FOUND] Moving clip still lost in void")

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
			if DEBUG_LOGGING:
				self.surface.log_message(
					f"[ANOMALY_ALERT] Possible deliberate renaming/moving detected (similarity: {similarity:.1%})"
				)

			# Can optionally send Live message notification
			self.surface.show_message("Clip parameters may be out of sync - check settings manually")

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


