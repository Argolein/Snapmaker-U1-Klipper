#!/usr/bin/env python3
# Analyze motor phase tuning accelerometer captures
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse
import bisect
import csv
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path


AXES = ("accel_x", "accel_y", "accel_z")
DEFAULT_DIRECT_SHIFT_SCALE_DEG = 10.0
FILENAME_META_RE = re.compile(
    r".*_(forward|backward)_([0-9]+(?:p[0-9]+)?)\.csv$")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze constant-speed motor phase calibration CSV files.")
    parser.add_argument(
        "csv_files", nargs="+", help="CSV files written by MOTOR_PHASE_MEASURE")
    parser.add_argument(
        "--speed-mm-s", type=float, required=True,
        help="Measurement move speed in mm/s")
    parser.add_argument(
        "--distance-mm", type=float, default=None,
        help="Measurement move distance in mm; enables exact-length windowing")
    parser.add_argument(
        "--rotation-distance", type=float, default=40.0,
        help="Stepper rotation_distance in mm (default: 40.0)")
    parser.add_argument(
        "--full-steps-per-rotation", type=int, default=200,
        help="Stepper full_steps_per_rotation (default: 200)")
    parser.add_argument(
        "--axis", choices=("auto",) + AXES, default="auto",
        help="Axis to analyze, or auto-select by fundamental response")
    parser.add_argument(
        "--harmonics", type=int, default=8,
        help="Number of electrical harmonics to analyze (default: 8)")
    parser.add_argument(
        "--min-samples-per-cycle", type=float, default=10.0,
        help="Target lower bound for robust harmonic analysis (default: 10.0)")
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of text")
    parser.add_argument(
        "--trim-mode", choices=("auto", "none"), default="auto",
        help="Trim settle-time regions from the CSV before analysis")
    parser.add_argument(
        "--trim-threshold-ratio", type=float, default=0.15,
        help="Relative motion threshold above baseline for auto trimming")
    parser.add_argument(
        "--trim-window", type=int, default=9,
        help="Moving-average window for auto trimming (default: 9)")
    parser.add_argument(
        "--compare-fb", action="store_true",
        help="Compare forward/backward file pairs grouped by speed")
    parser.add_argument(
        "--aggregate-fb", action="store_true",
        help="Aggregate multiple forward/backward runs per speed before comparing")
    parser.add_argument(
        "--export-basis", action="store_true",
        help="Export a small forward/backward harmonic basis from aggregated runs")
    parser.add_argument(
        "--basis-harmonics", type=int, default=4,
        help="Maximum number of low-order harmonics to include in the basis "
             "(default: 4)")
    parser.add_argument(
        "--basis-ratio-threshold", type=float, default=2.5,
        help="Maximum forward/backward magnitude ratio for a harmonic to be "
             "marked stable in the basis (default: 2.5)")
    parser.add_argument(
        "--export-fit", action="store_true",
        help="Export a first normalized forward/backward fit and LUT from the basis")
    parser.add_argument(
        "--fit-points", type=int, default=1024,
        help="Number of electrical phase points in the exported LUT (default: 1024)")
    parser.add_argument(
        "--export-runtime-payload", action="store_true",
        help="Export a runtime-oriented prototype payload from the normalized fit")
    parser.add_argument(
        "--trace-file", type=str, default=None,
        help="Trace CSV written by MOTOR_PHASE_CAPTURE_SYNC or MOTOR_PHASE_STEP_TRACE")
    parser.add_argument(
        "--runtime-trace-file", type=str, default=None,
        help="Runtime flush-trace CSV written by MOTOR_PHASE_CAPTURE_SYNC or "
             "MOTOR_PHASE_STEP_TRACE")
    parser.add_argument(
        "--mcu-stats-file", type=str, default=None,
        help="MCU stepper runtime stats CSV written by MOTOR_PHASE_CAPTURE_SYNC "
             "or MOTOR_PHASE_STEP_TRACE")
    parser.add_argument(
        "--exec-trace-file", type=str, default=None,
        help="MCU execution-trace CSV written by MOTOR_PHASE_CAPTURE_SYNC "
             "or MOTOR_PHASE_STEP_TRACE")
    parser.add_argument(
        "--correction-plan-file", type=str, default=None,
        help="Execution-near correction plan CSV written by "
             "MOTOR_PHASE_CAPTURE_SYNC")
    parser.add_argument(
        "--correction-plan-compare-harmonics", type=str, default=None,
        help="Comma-separated low-order harmonics for an additional filtered "
             "plan-vs-residual comparison (example: 2,4). When omitted, the "
             "comparison auto-uses the residual curve's recommended low-order "
             "harmonics.")
    parser.add_argument(
        "--phase-bins", type=int, default=64,
        help="Number of electrical phase bins for paired accel/trace analysis "
             "(default: 64)")
    parser.add_argument(
        "--export-phase-residual", action="store_true",
        help="Export an interpolated phase-binned residual curve from one "
             "paired accel/trace capture")
    parser.add_argument(
        "--phase-residual-points", type=int, default=1024,
        help="Number of points in the exported residual curve "
             "(default: 1024)")
    parser.add_argument(
        "--phase-residual-min-bin-count", type=int, default=1,
        help="Minimum per-bin sample count required for a residual run to be "
             "accepted into aggregation (default: 1)")
    parser.add_argument(
        "--phase-residual-smoothing-window", type=int, default=9,
        help="Circular moving-average window used for residual alignment and "
             "smoothed aggregate output (default: 9)")
    parser.add_argument(
        "--phase-residual-quality-threshold", type=float, default=0.55,
        help="Minimum normalized run-quality score required for a residual "
             "capture to remain in aggregation (default: 0.55)")
    parser.add_argument(
        "--phase-residual-quality-harmonics", type=int, default=4,
        help="Number of low-order residual harmonics to inspect for run "
             "quality scoring (default: 4)")
    parser.add_argument(
        "--phase-residual-align",
        choices=("auto", "correlation", "h1", "peak", "harmonic"),
        default="auto",
        help="Alignment strategy for repeated residual aggregation "
             "(default: auto)")
    parser.add_argument(
        "--phase-residual-align-harmonic", type=int, default=2,
        help="Specific harmonic to lock onto when "
             "--phase-residual-align=harmonic (default: 2)")
    parser.add_argument(
        "--aggregate-phase-residual", action="store_true",
        help="Auto-resolve trace files for multiple sync accel captures and "
            "aggregate their exported residual curves")
    parser.add_argument(
        "--aggregate-correction-plan-residual", action="store_true",
        help="Auto-resolve matching correction-plan CSVs for multiple sync "
             "captures and aggregate plan-vs-residual comparisons")
    parser.add_argument(
        "--candidate-residual-out", type=str, default=None,
        help="Write the aggregated candidate residual artifact as JSON to the "
             "given path")
    parser.add_argument(
        "--candidate-reference", type=str, default=None,
        help="Compare the aggregated candidate residual against an existing "
             "frozen candidate artifact JSON")
    parser.add_argument(
        "--candidate-reference-refresh-q15-mean-abs", type=float, default=256.0,
        help="Minimum mean absolute q15 delta against the frozen reference "
             "before a candidate is considered materially different "
             "(default: 256)")
    parser.add_argument(
        "--candidate-reference-refresh-phase-delta-deg", type=float,
        default=20.0,
        help="Minimum per-harmonic phase delta in degrees to count as a "
             "material low-order harmonic shift against the frozen reference "
             "(default: 20.0)")
    parser.add_argument(
        "--candidate-residual-harmonics", type=int, default=4,
        help="Number of low-order harmonics to summarize in the exported "
             "candidate residual artifact (default: 4)")
    parser.add_argument(
        "--candidate-residual-share-threshold", type=float, default=0.15,
        help="Minimum magnitude share for a candidate residual harmonic to be "
             "recommended in the compact artifact summary (default: 0.15)")
    return parser.parse_args()


def parse_filename_metadata(path):
    match = FILENAME_META_RE.match(path.name)
    if match is None:
        return {}
    raw_speed = match.group(2).replace("p", ".")
    return {
        "direction": match.group(1),
        "speed_mm_s": float(raw_speed),
    }


def load_csv(path):
    rows = []
    with open(path, newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row:
                continue
            if row[0].startswith("#"):
                row[0] = row[0].lstrip("#")
            if row[0] == "time":
                continue
            rows.append(tuple(float(cell) for cell in row[:4]))
    if len(rows) < 3:
        raise ValueError("Need at least 3 samples")
    times = [row[0] for row in rows]
    axes = {
        "accel_x": [row[1] for row in rows],
        "accel_y": [row[2] for row in rows],
        "accel_z": [row[3] for row in rows],
    }
    return times, axes


def load_trace_csv(path):
    rows = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            rows.append({
                "step_time": float(row["step_time"]),
                "step_clock": int(row["step_clock"]),
                "mcu_position": int(row["mcu_position"]),
                "commanded_position_mm": float(row["commanded_position_mm"]),
                "phase_index": int(row["phase_index"]),
                "segment_index": int(row["segment_index"]),
                "step_in_segment": int(row["step_in_segment"]),
                "segment_first_clock": int(row["segment_first_clock"]),
                "segment_last_clock": int(row["segment_last_clock"]),
                "segment_interval": int(row["segment_interval"]),
                "segment_add": int(row["segment_add"]),
                "segment_step_count": int(row["segment_step_count"]),
            })
    if len(rows) < 3:
        raise ValueError("Need at least 3 trace samples")
    return rows


def load_runtime_trace_csv(path):
    rows = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            rows.append({
                "flush_index": int(row["flush_index"]),
                "window_start_print_time": float(row["window_start_print_time"]),
                "window_end_print_time": float(row["window_end_print_time"]),
                "window_duration_s": float(row["window_duration_s"]),
                "before_commanded_position_mm": float(
                    row["before_commanded_position_mm"]),
                "after_commanded_position_mm": float(
                    row["after_commanded_position_mm"]),
                "commanded_delta_mm": float(row["commanded_delta_mm"]),
                "before_mcu_position": int(row["before_mcu_position"]),
                "after_mcu_position": int(row["after_mcu_position"]),
                "generated_steps": int(row["generated_steps"]),
                "generated_distance_mm": float(row["generated_distance_mm"]),
                "start_phase_index": int(row["start_phase_index"]),
                "end_phase_index": int(row["end_phase_index"]),
                "phase_delta": int(row["phase_delta"]),
            })
    if not rows:
        raise ValueError("Need at least 1 runtime-trace sample")
    return rows


def load_mcu_stats_csv(path):
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if len(rows) != 1:
        raise ValueError("Expected exactly 1 MCU stats row")
    row = rows[0]
    return {
        "stepper_name": row["stepper_name"],
        "queue_msgs": int(row["queue_msgs"]),
        "load_next": int(row["load_next"]),
        "timer_events": int(row["timer_events"]),
        "total_steps": int(row["total_steps"]),
        "max_chunk": int(row["max_chunk"]),
        "queued_moves": int(row["queued_moves"]),
    }


def load_exec_trace_csv(path):
    rows = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            rows.append({
                "sample_index": int(row["sample_index"]),
                "step_number": int(row["step_number"]),
                "step_clock": int(row["step_clock"]),
                "step_time": float(row["step_time"]),
                "delta_step_clock": int(row["delta_step_clock"]),
                "delta_step_time_s": float(row["delta_step_time_s"]),
                "delta_steps": int(row["delta_steps"]),
                "trace_stride": int(row["trace_stride"]),
                "total_steps": int(row["total_steps"]),
                "first_clock": int(row["first_clock"]),
                "last_clock": int(row["last_clock"]),
                "min_interval": int(row["min_interval"]),
                "max_interval": int(row["max_interval"]),
            })
    if not rows:
        raise ValueError("Need at least 1 exec-trace sample")
    return rows


def load_correction_plan_csv(path):
    rows = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            rows.append({
                "sample_index": int(row["sample_index"]),
                "step_number": int(row["step_number"]),
                "step_clock": int(row["step_clock"]),
                "step_time": float(row["step_time"]),
                "mcu_position": int(row["mcu_position"]),
                "commanded_position_mm": float(row["commanded_position_mm"]),
                "phase_index": int(row["phase_index"]),
                "profile_representation": row["profile_representation"],
                "profile_phase_offset_q15": int(
                    row["profile_phase_offset_q15"]),
                "profile_shift_norm": float(row["profile_shift_norm"]),
                "profile_coil_a": int(row["profile_coil_a"]),
                "profile_coil_b": int(row["profile_coil_b"]),
                "baseline_coil_a": int(row["baseline_coil_a"]),
                "baseline_coil_b": int(row["baseline_coil_b"]),
                "delta_coil_a": int(row["delta_coil_a"]),
                "delta_coil_b": int(row["delta_coil_b"]),
            })
    if not rows:
        raise ValueError("Need at least 1 correction-plan sample")
    return rows


def auto_resolve_trace_path(accel_path):
    accel_name = accel_path.name
    if not accel_name.endswith("-accel.csv"):
        raise ValueError(
            "Auto trace resolution requires an accel filename ending in "
            "'-accel.csv': %s" % (accel_path,))
    trace_name = accel_name[:-10] + "-trace.csv"
    trace_path = accel_path.with_name(trace_name)
    if not trace_path.exists():
        raise ValueError(
            "Could not resolve matching trace file for %s -> %s" % (
                accel_path, trace_path))
    return trace_path


def auto_resolve_correction_plan_path(accel_path):
    accel_name = accel_path.name
    if not accel_name.endswith("-accel.csv"):
        raise ValueError(
            "Auto correction-plan resolution requires an accel filename "
            "ending in '-accel.csv': %s" % (accel_path,))
    plan_name = accel_name[:-10] + "-plan.csv"
    plan_path = accel_path.with_name(plan_name)
    if not plan_path.exists():
        raise ValueError(
            "Could not resolve matching correction plan file for %s -> %s" % (
                accel_path, plan_path))
    return plan_path


def analyze_runtime_trace(path):
    rows = load_runtime_trace_csv(path)
    active_rows = [row for row in rows if row["generated_steps"]]
    zero_rows = [row for row in rows if not row["generated_steps"]]
    total_duration = max(
        0.0,
        rows[-1]["window_end_print_time"] - rows[0]["window_start_print_time"])
    active_duration = 0.0
    if active_rows:
        active_duration = max(
            0.0,
            active_rows[-1]["window_end_print_time"]
            - active_rows[0]["window_start_print_time"])
    window_durations = [row["window_duration_s"] for row in rows]
    active_window_durations = [row["window_duration_s"] for row in active_rows]
    abs_phase_deltas = [abs(row["phase_delta"]) for row in active_rows]
    abs_generated_steps = [abs(row["generated_steps"]) for row in active_rows]
    abs_generated_distances = [
        abs(row["generated_distance_mm"]) for row in active_rows]
    return {
        "path": str(path),
        "flush_count": len(rows),
        "active_flush_count": len(active_rows),
        "zero_step_flush_count": len(zero_rows),
        "total_duration_s": total_duration,
        "active_duration_s": active_duration,
        "flush_rate_hz": len(rows) / total_duration if total_duration > 0.0 else 0.0,
        "active_flush_rate_hz": (
            len(active_rows) / active_duration if active_duration > 0.0 else 0.0),
        "window_duration_s": {
            "mean": statistics.fmean(window_durations),
            "max": max(window_durations),
        },
        "active_window_duration_s": {
            "mean": (
                statistics.fmean(active_window_durations)
                if active_window_durations else 0.0),
            "max": max(active_window_durations) if active_window_durations else 0.0,
        },
        "generated_steps": {
            "mean_abs": (
                statistics.fmean(abs_generated_steps)
                if abs_generated_steps else 0.0),
            "max_abs": max(abs_generated_steps) if abs_generated_steps else 0,
            "total_abs": sum(abs_generated_steps),
        },
        "generated_distance_mm": {
            "mean_abs": (
                statistics.fmean(abs_generated_distances)
                if abs_generated_distances else 0.0),
            "max_abs": max(abs_generated_distances)
            if abs_generated_distances else 0.0,
            "total_abs": sum(abs_generated_distances),
        },
        "phase_delta": {
            "mean_abs": (
                statistics.fmean(abs_phase_deltas)
                if abs_phase_deltas else 0.0),
            "max_abs": max(abs_phase_deltas) if abs_phase_deltas else 0,
        },
        "preview": rows[:16],
    }


def analyze_mcu_stats(path):
    row = load_mcu_stats_csv(path)
    steps_per_load = (
        row["total_steps"] / row["load_next"] if row["load_next"] else 0.0)
    steps_per_queue_msg = (
        row["total_steps"] / row["queue_msgs"] if row["queue_msgs"] else 0.0)
    timer_events_per_step = (
        row["timer_events"] / row["total_steps"] if row["total_steps"] else 0.0)
    return {
        "path": str(path),
        "stepper_name": row["stepper_name"],
        "queue_msgs": row["queue_msgs"],
        "load_next": row["load_next"],
        "timer_events": row["timer_events"],
        "total_steps": row["total_steps"],
        "max_chunk": row["max_chunk"],
        "queued_moves": row["queued_moves"],
        "steps_per_load": steps_per_load,
        "steps_per_queue_msg": steps_per_queue_msg,
        "timer_events_per_step": timer_events_per_step,
    }


def analyze_exec_trace(path):
    rows = load_exec_trace_csv(path)
    delta_step_times = [row["delta_step_time_s"] for row in rows[1:]]
    delta_steps = [row["delta_steps"] for row in rows[1:]]
    delta_clocks = [row["delta_step_clock"] for row in rows[1:]]
    span_s = max(0.0, rows[-1]["step_time"] - rows[0]["step_time"])
    span_steps = max(0, rows[-1]["step_number"] - rows[0]["step_number"])
    sample_rate_hz = ((len(rows) - 1) / span_s) if span_s > 0.0 else 0.0
    observed_step_rate_hz = (span_steps / span_s) if span_s > 0.0 else 0.0
    return {
        "path": str(path),
        "sample_count": len(rows),
        "trace_stride": rows[0]["trace_stride"],
        "total_steps": rows[0]["total_steps"],
        "span_s": span_s,
        "span_steps": span_steps,
        "sample_rate_hz": sample_rate_hz,
        "observed_step_rate_hz": observed_step_rate_hz,
        "delta_steps": summarize_numeric(delta_steps) if delta_steps else {
            "mean": 0.0, "min": 0, "max": 0, "count": 0, "stdev": 0.0},
        "delta_step_time_s": (
            summarize_numeric(delta_step_times) if delta_step_times else {
                "mean": 0.0, "min": 0.0, "max": 0.0, "count": 0, "stdev": 0.0}),
        "delta_step_clock": (
            summarize_numeric(delta_clocks) if delta_clocks else {
                "mean": 0.0, "min": 0, "max": 0, "count": 0, "stdev": 0.0}),
        "min_interval_clock": rows[0]["min_interval"],
        "max_interval_clock": rows[0]["max_interval"],
        "preview": rows[:16],
    }


def analyze_correction_plan(path):
    rows = load_correction_plan_csv(path)
    abs_delta_coils = [
        max(abs(row["delta_coil_a"]), abs(row["delta_coil_b"]))
        for row in rows
    ]
    abs_shift_norm = [abs(row["profile_shift_norm"]) for row in rows]
    phase_indexes = [row["phase_index"] for row in rows]
    representations = sorted({
        row["profile_representation"] for row in rows
    })
    delta_coil_a = [row["delta_coil_a"] for row in rows]
    delta_coil_b = [row["delta_coil_b"] for row in rows]
    return {
        "path": str(path),
        "sample_count": len(rows),
        "phase_index_min": min(phase_indexes),
        "phase_index_max": max(phase_indexes),
        "representations": representations,
        "mean_abs_delta_coil": statistics.fmean(abs_delta_coils),
        "max_abs_delta_coil": max(abs_delta_coils),
        "mean_abs_shift_norm": statistics.fmean(abs_shift_norm),
        "max_abs_shift_norm": max(abs_shift_norm),
        "delta_coil_a": summarize_numeric(delta_coil_a),
        "delta_coil_b": summarize_numeric(delta_coil_b),
        "preview": rows[:16],
    }


def normalize_signed_curve(values):
    peak_abs = max((abs(value) for value in values), default=0.0)
    if peak_abs <= 0.0:
        return [0.0] * len(values), 0.0
    return [value / peak_abs for value in values], peak_abs


def normalized_dot(reference, candidate):
    ref_norm = math.sqrt(sum(value * value for value in reference))
    cand_norm = math.sqrt(sum(value * value for value in candidate))
    if ref_norm <= 0.0 or cand_norm <= 0.0:
        return 0.0
    return sum(a * b for a, b in zip(reference, candidate)) / (
        ref_norm * cand_norm)


def parse_harmonic_list(raw_value):
    if raw_value is None:
        return None
    harmonics = []
    for token in raw_value.split(","):
        token = token.strip()
        if not token:
            continue
        harmonic = int(token)
        if harmonic <= 0:
            raise ValueError("Harmonics must be positive integers")
        harmonics.append(harmonic)
    if not harmonics:
        raise ValueError("Need at least one harmonic")
    return sorted(set(harmonics))


def build_harmonic_only_curve(values, harmonics):
    total = len(values)
    reconstructed = [0.0] * total
    descriptors = {
        harmonic: harmonic_descriptor(values, harmonic)
        for harmonic in harmonics
    }
    for harmonic, descriptor in descriptors.items():
        amplitude = descriptor["magnitude"] * 2.0
        phase_rad = math.radians(descriptor["phase_deg"])
        for index in range(total):
            angle = 2.0 * math.pi * harmonic * index / total
            reconstructed[index] += amplitude * math.cos(angle + phase_rad)
    return reconstructed, descriptors


def compare_normalized_curves(reference_curve, candidate_curve):
    normal_shift, _ = best_circular_shift(reference_curve, candidate_curve)
    shifted_normal = circular_shift(candidate_curve, normal_shift)
    normal_score = normalized_dot(reference_curve, shifted_normal)
    inverted_curve = [-value for value in candidate_curve]
    inverted_shift, _ = best_circular_shift(reference_curve, inverted_curve)
    shifted_inverted = circular_shift(inverted_curve, inverted_shift)
    inverted_score = normalized_dot(reference_curve, shifted_inverted)
    if abs(inverted_score) > abs(normal_score):
        selected_polarity = "inverted"
        selected_shift = inverted_shift
        selected_score = inverted_score
        selected_curve = shifted_inverted
    else:
        selected_polarity = "normal"
        selected_shift = normal_shift
        selected_score = normal_score
        selected_curve = shifted_normal
    delta = [
        reference - candidate
        for reference, candidate in zip(reference_curve, selected_curve)
    ]
    return {
        "normal_alignment": {
            "shift": normal_shift,
            "score": normal_score,
        },
        "inverted_alignment": {
            "shift": inverted_shift,
            "score": inverted_score,
        },
        "selected_alignment": {
            "polarity": selected_polarity,
            "shift": selected_shift,
            "score": selected_score,
        },
        "aligned_candidate_curve": selected_curve,
        "delta_curve": delta,
        "delta_summary": {
            "mean_abs": statistics.fmean(abs(value) for value in delta),
            "rms": math.sqrt(statistics.fmean(value * value for value in delta)),
            "max_abs": max(abs(value) for value in delta),
        },
    }


def build_correction_plan_shift_curve(path, point_count, smoothing_window):
    rows = load_correction_plan_csv(path)
    by_phase = defaultdict(list)
    representations = set()
    for row in rows:
        phase_index = int(row["phase_index"]) % point_count
        by_phase[phase_index].append(float(row["profile_shift_norm"]))
        representations.add(row["profile_representation"])
    sparse = [None] * point_count
    for phase_index, values in by_phase.items():
        sparse[phase_index] = statistics.fmean(values)
    filled = fill_missing_circular(sparse)
    smoothed = circular_moving_average(filled, smoothing_window)
    norm_curve, peak_abs = normalize_signed_curve(smoothed)
    return {
        "path": str(path),
        "sample_count": len(rows),
        "populated_phase_count": len(by_phase),
        "phase_index_min": min(by_phase) if by_phase else 0,
        "phase_index_max": max(by_phase) if by_phase else 0,
        "representations": sorted(representations),
        "shift_curve": smoothed,
        "shift_curve_norm": norm_curve,
        "shift_curve_peak_abs": peak_abs,
    }


def compare_correction_plan_to_phase_residual(correction_plan_path,
                                              phase_residual, opts):
    point_count = phase_residual["phase_residual_points"]
    plan_curve = build_correction_plan_shift_curve(
        correction_plan_path, point_count,
        opts.phase_residual_smoothing_window)
    residual_curve = list(
        phase_residual["interpolated_residual"]["residual_norm"])
    raw_compare = compare_normalized_curves(
        residual_curve, plan_curve["shift_curve_norm"])
    harmonic_source = "auto_residual_recommended"
    selected_harmonics = parse_harmonic_list(
        opts.correction_plan_compare_harmonics)
    if selected_harmonics is None:
        harmonic_summary = summarize_candidate_residual_harmonics(
            residual_curve, opts)
        selected_harmonics = list(
            harmonic_summary["recommended_harmonics"] or
            [harmonic_summary["harmonics"][0]["harmonic"]])
    else:
        harmonic_source = "explicit"
    residual_harmonic_curve, residual_harmonic_descriptors = (
        build_harmonic_only_curve(residual_curve, selected_harmonics))
    plan_harmonic_curve, plan_harmonic_descriptors = (
        build_harmonic_only_curve(
            plan_curve["shift_curve_norm"], selected_harmonics))
    residual_harmonic_norm, residual_harmonic_peak = normalize_signed_curve(
        residual_harmonic_curve)
    plan_harmonic_norm, plan_harmonic_peak = normalize_signed_curve(
        plan_harmonic_curve)
    harmonic_compare = compare_normalized_curves(
        residual_harmonic_norm, plan_harmonic_norm)
    return {
        "correction_plan_file": str(correction_plan_path),
        "selected_axis": phase_residual["selected_axis"],
        "phase_points": point_count,
        "plan_populated_phase_count": plan_curve["populated_phase_count"],
        "plan_phase_index_min": plan_curve["phase_index_min"],
        "plan_phase_index_max": plan_curve["phase_index_max"],
        "plan_representations": plan_curve["representations"],
        "plan_peak_abs": plan_curve["shift_curve_peak_abs"],
        "normal_alignment": {
            "shift": raw_compare["normal_alignment"]["shift"],
            "score": raw_compare["normal_alignment"]["score"],
        },
        "inverted_alignment": {
            "shift": raw_compare["inverted_alignment"]["shift"],
            "score": raw_compare["inverted_alignment"]["score"],
        },
        "selected_alignment": {
            "polarity": raw_compare["selected_alignment"]["polarity"],
            "shift": raw_compare["selected_alignment"]["shift"],
            "score": raw_compare["selected_alignment"]["score"],
        },
        "residual_curve_norm": residual_curve,
        "aligned_plan_curve_norm": raw_compare["aligned_candidate_curve"],
        "delta_curve": raw_compare["delta_curve"],
        "delta_summary": raw_compare["delta_summary"],
        "harmonic_compare": {
            "harmonics": selected_harmonics,
            "harmonic_source": harmonic_source,
            "residual_peak_abs": residual_harmonic_peak,
            "plan_peak_abs": plan_harmonic_peak,
            "residual_descriptors": [
                residual_harmonic_descriptors[harmonic]
                for harmonic in selected_harmonics
            ],
            "plan_descriptors": [
                plan_harmonic_descriptors[harmonic]
                for harmonic in selected_harmonics
            ],
            "normal_alignment": harmonic_compare["normal_alignment"],
            "inverted_alignment": harmonic_compare["inverted_alignment"],
            "selected_alignment": harmonic_compare["selected_alignment"],
            "residual_curve_norm": residual_harmonic_norm,
            "aligned_plan_curve_norm": harmonic_compare["aligned_candidate_curve"],
            "delta_curve": harmonic_compare["delta_curve"],
            "delta_summary": harmonic_compare["delta_summary"],
            "preview": {
                "residual_norm_0_16": residual_harmonic_norm[:16],
                "aligned_plan_norm_0_16":
                    harmonic_compare["aligned_candidate_curve"][:16],
                "delta_0_16": harmonic_compare["delta_curve"][:16],
            },
        },
        "preview": {
            "residual_norm_0_16": residual_curve[:16],
            "aligned_plan_norm_0_16": raw_compare["aligned_candidate_curve"][:16],
            "delta_0_16": raw_compare["delta_curve"][:16],
        },
        "notes": (
            "Offline comparison between the interpolated phase residual and "
            "the execution-near projected plan shift curve. The report now "
            "includes both a raw normalized comparison and an additional "
            "low-order harmonic-only comparison. This still indicates shape "
            "agreement only; it is not yet a runtime benefit test."
        ),
    }


def build_correction_plan_residual_aggregate(items):
    if not items:
        raise ValueError("Need at least one correction-plan residual comparison")
    phase_points = items[0]["phase_points"]
    residual_curves = [item["residual_curve_norm"] for item in items]
    aligned_plan_curves = [item["aligned_plan_curve_norm"] for item in items]
    delta_curves = [item["delta_curve"] for item in items]
    harmonic_residual_curves = [
        item["harmonic_compare"]["residual_curve_norm"] for item in items]
    harmonic_plan_curves = [
        item["harmonic_compare"]["aligned_plan_curve_norm"] for item in items]
    harmonic_delta_curves = [
        item["harmonic_compare"]["delta_curve"] for item in items]
    mean_residual = [
        statistics.fmean(curve[index] for curve in residual_curves)
        for index in range(phase_points)
    ]
    mean_plan = [
        statistics.fmean(curve[index] for curve in aligned_plan_curves)
        for index in range(phase_points)
    ]
    mean_delta = [
        statistics.fmean(curve[index] for curve in delta_curves)
        for index in range(phase_points)
    ]
    stdev_delta = [
        statistics.stdev([curve[index] for curve in delta_curves])
        if len(delta_curves) > 1 else 0.0
        for index in range(phase_points)
    ]
    harmonic_mean_residual = [
        statistics.fmean(curve[index] for curve in harmonic_residual_curves)
        for index in range(phase_points)
    ]
    harmonic_mean_plan = [
        statistics.fmean(curve[index] for curve in harmonic_plan_curves)
        for index in range(phase_points)
    ]
    harmonic_mean_delta = [
        statistics.fmean(curve[index] for curve in harmonic_delta_curves)
        for index in range(phase_points)
    ]
    harmonic_stdev_delta = [
        statistics.stdev([curve[index] for curve in harmonic_delta_curves])
        if len(harmonic_delta_curves) > 1 else 0.0
        for index in range(phase_points)
    ]
    return {
        "capture_count": len(items),
        "selected_axis_mode": statistics.multimode(
            [item["selected_axis"] for item in items])[0],
        "selected_polarity_mode": statistics.multimode(
            [item["selected_alignment"]["polarity"] for item in items])[0],
        "phase_points": phase_points,
        "plan_populated_phase_count_mode": statistics.multimode(
            [item["plan_populated_phase_count"] for item in items])[0],
        "plan_representations": sorted({
            representation
            for item in items
            for representation in item["plan_representations"]
        }),
        "selected_score": summarize_numeric(
            [item["selected_alignment"]["score"] for item in items]),
        "harmonic_selected_score": summarize_numeric(
            [item["harmonic_compare"]["selected_alignment"]["score"]
             for item in items]),
        "mean_abs_delta": summarize_numeric(
            [item["delta_summary"]["mean_abs"] for item in items]),
        "harmonic_mean_abs_delta": summarize_numeric(
            [item["harmonic_compare"]["delta_summary"]["mean_abs"]
             for item in items]),
        "rms_delta": summarize_numeric(
            [item["delta_summary"]["rms"] for item in items]),
        "harmonic_rms_delta": summarize_numeric(
            [item["harmonic_compare"]["delta_summary"]["rms"]
             for item in items]),
        "max_abs_delta": summarize_numeric(
            [item["delta_summary"]["max_abs"] for item in items]),
        "harmonic_max_abs_delta": summarize_numeric(
            [item["harmonic_compare"]["delta_summary"]["max_abs"]
             for item in items]),
        "selected_shift": summarize_numeric(
            [item["selected_alignment"]["shift"] for item in items]),
        "harmonic_selected_shift": summarize_numeric(
            [item["harmonic_compare"]["selected_alignment"]["shift"]
             for item in items]),
        "harmonic_set_mode": statistics.multimode([
            tuple(item["harmonic_compare"]["harmonics"]) for item in items])[0],
        "harmonic_source_mode": statistics.multimode([
            item["harmonic_compare"]["harmonic_source"] for item in items])[0],
        "harmonic_selected_polarity_mode": statistics.multimode([
            item["harmonic_compare"]["selected_alignment"]["polarity"]
            for item in items])[0],
        "mean_residual_curve": mean_residual,
        "mean_aligned_plan_curve": mean_plan,
        "mean_delta_curve": mean_delta,
        "stdev_delta_curve": stdev_delta,
        "harmonic_mean_residual_curve": harmonic_mean_residual,
        "harmonic_mean_aligned_plan_curve": harmonic_mean_plan,
        "harmonic_mean_delta_curve": harmonic_mean_delta,
        "harmonic_stdev_delta_curve": harmonic_stdev_delta,
        "source_files": [
            {
                "correction_plan_file": item["correction_plan_file"],
                "polarity": item["selected_alignment"]["polarity"],
                "score": item["selected_alignment"]["score"],
                "mean_abs_delta": item["delta_summary"]["mean_abs"],
                "rms_delta": item["delta_summary"]["rms"],
                "harmonic_polarity":
                    item["harmonic_compare"]["selected_alignment"]["polarity"],
                "harmonic_score":
                    item["harmonic_compare"]["selected_alignment"]["score"],
                "harmonic_mean_abs_delta":
                    item["harmonic_compare"]["delta_summary"]["mean_abs"],
                "harmonic_rms_delta":
                    item["harmonic_compare"]["delta_summary"]["rms"],
            }
            for item in items
        ],
        "preview": {
            "mean_residual_curve_0_16": mean_residual[:16],
            "mean_aligned_plan_curve_0_16": mean_plan[:16],
            "mean_delta_curve_0_16": mean_delta[:16],
            "stdev_delta_curve_0_16": stdev_delta[:16],
            "harmonic_mean_residual_curve_0_16": harmonic_mean_residual[:16],
            "harmonic_mean_aligned_plan_curve_0_16": harmonic_mean_plan[:16],
            "harmonic_mean_delta_curve_0_16": harmonic_mean_delta[:16],
            "harmonic_stdev_delta_curve_0_16": harmonic_stdev_delta[:16],
        },
        "notes": (
            "Aggregate offline comparison across multiple projected "
            "execution-near plans and matched residual curves at one working "
            "point. The aggregate now keeps both the raw normalized curve "
            "comparison and a low-order harmonic-only comparison. This is "
            "still a shape-consistency diagnostic, not a runtime benefit "
            "measurement."
        ),
    }


def moving_average(values, window):
    if window <= 1:
        return list(values)
    half = window // 2
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    averaged = []
    count = len(values)
    for index in range(count):
        start = max(0, index - half)
        end = min(count, index + half + 1)
        averaged.append((prefix[end] - prefix[start]) / (end - start))
    return averaged


def detect_active_window(times, axis_values, threshold_ratio, window):
    magnitudes = []
    for xyz in zip(axis_values["accel_x"], axis_values["accel_y"],
                   axis_values["accel_z"]):
        magnitudes.append(math.sqrt(sum(value * value for value in xyz)))
    smoothed = moving_average(magnitudes, window)
    baseline_count = max(10, min(len(smoothed) // 8, 50))
    baseline = statistics.median(smoothed[:baseline_count])
    peak = max(smoothed)
    threshold = baseline + threshold_ratio * (peak - baseline)
    active_indices = [
        index for index, value in enumerate(smoothed) if value >= threshold
    ]
    if not active_indices:
        return 0, len(times), {
            "baseline": baseline,
            "peak": peak,
            "threshold": threshold,
            "trimmed": False,
        }
    start_index = active_indices[0]
    end_index = active_indices[-1] + 1
    return start_index, end_index, {
        "baseline": baseline,
        "peak": peak,
        "threshold": threshold,
        "trimmed": True,
    }


def detect_expected_duration_window(times, axis_values, duration_s, window):
    if duration_s <= 0.0:
        raise ValueError("duration_s must be positive")
    magnitudes = []
    for xyz in zip(axis_values["accel_x"], axis_values["accel_y"],
                   axis_values["accel_z"]):
        magnitudes.append(math.sqrt(sum(value * value for value in xyz)))
    smoothed = moving_average(magnitudes, window)
    sample_intervals = [b - a for a, b in zip(times, times[1:])]
    median_dt = statistics.median(sample_intervals)
    window_samples = max(3, int(round(duration_s / median_dt)) + 1)
    if window_samples >= len(times):
        return 0, len(times), {
            "trimmed": False,
            "baseline": statistics.median(smoothed[:max(1, min(len(smoothed), 50))]),
            "window_samples": window_samples,
            "median_dt": median_dt,
        }
    baseline_count = max(10, min(len(smoothed) // 8, 50))
    baseline = statistics.median(smoothed[:baseline_count])
    weights = [value - baseline for value in smoothed]
    prefix = [0.0]
    for value in weights:
        prefix.append(prefix[-1] + value)
    best_start = 0
    best_score = None
    last_start = len(times) - window_samples
    for start in range(last_start + 1):
        end = start + window_samples
        score = prefix[end] - prefix[start]
        if best_score is None or score > best_score:
            best_score = score
            best_start = start
    best_end = best_start + window_samples
    return best_start, best_end, {
        "baseline": baseline,
        "best_score": best_score,
        "window_samples": window_samples,
        "median_dt": median_dt,
        "trimmed": True,
    }


def single_frequency_response(times, values, frequency_hz):
    mean_value = statistics.fmean(values)
    real_sum = 0.0
    imag_sum = 0.0
    count = len(values)
    for t, value in zip(times, values):
        centered = value - mean_value
        angle = 2.0 * math.pi * frequency_hz * t
        real_sum += centered * math.cos(angle)
        imag_sum -= centered * math.sin(angle)
    magnitude = (2.0 / count) * math.hypot(real_sum, imag_sum)
    phase_deg = math.degrees(math.atan2(imag_sum, real_sum))
    rms = math.sqrt(sum((value - mean_value) ** 2 for value in values) / count)
    return {
        "mean": mean_value,
        "rms": rms,
        "magnitude": magnitude,
        "phase_deg": phase_deg,
    }


def analyze_axis(times, values, electrical_hz, harmonics):
    result = single_frequency_response(times, values, electrical_hz)
    harmonic_data = []
    for harmonic in range(1, harmonics + 1):
        harmonic_data.append({
            "harmonic": harmonic,
            "frequency_hz": electrical_hz * harmonic,
            **single_frequency_response(
                times, values, electrical_hz * harmonic),
        })
    result["harmonics"] = harmonic_data
    return result


def analyze_file(path, opts):
    times, axis_values = load_csv(path)
    raw_sample_count = len(times)
    raw_duration = times[-1] - times[0]
    if opts.trim_mode == "auto":
        if opts.distance_mm is not None:
            start_index, end_index, trim_info = detect_expected_duration_window(
                times, axis_values, opts.distance_mm / opts.speed_mm_s,
                opts.trim_window)
        else:
            start_index, end_index, trim_info = detect_active_window(
                times, axis_values, opts.trim_threshold_ratio,
                opts.trim_window)
        times = times[start_index:end_index]
        axis_values = {
            axis: values[start_index:end_index]
            for axis, values in axis_values.items()
        }
    else:
        start_index, end_index = 0, len(times)
        trim_info = {"trimmed": False}
    duration = times[-1] - times[0]
    if duration <= 0.0:
        raise ValueError("Invalid sample timestamps")
    sample_count = len(times)
    sample_rate = (sample_count - 1) / duration
    sample_intervals = [
        times[index + 1] - times[index] for index in range(sample_count - 1)
    ]
    electrical_cycle_mm = (
        opts.rotation_distance / opts.full_steps_per_rotation * 4.0)
    electrical_hz = opts.speed_mm_s / electrical_cycle_mm
    samples_per_cycle = sample_rate / electrical_hz
    nyquist_harmonic_limit = (sample_rate * 0.5) / electrical_hz
    analyses = {
        axis: analyze_axis(times, values, electrical_hz, opts.harmonics)
        for axis, values in axis_values.items()
    }
    selected_axis = opts.axis
    if selected_axis == "auto":
        selected_axis = max(
            AXES,
            key=lambda axis: analyses[axis]["harmonics"][0]["magnitude"])
    recommended_max_speed = (
        sample_rate * electrical_cycle_mm / opts.min_samples_per_cycle)
    return {
        "file": str(path),
        "filename_meta": parse_filename_metadata(path),
        "raw_sample_count": raw_sample_count,
        "raw_duration_s": raw_duration,
        "sample_count": sample_count,
        "duration_s": duration,
        "sample_rate_hz": sample_rate,
        "median_dt_s": statistics.median(sample_intervals),
        "trim_mode": opts.trim_mode,
        "trim_start_index": start_index,
        "trim_end_index": end_index,
        "trim_start_time_s": times[0],
        "trim_end_time_s": times[-1],
        "trim_info": trim_info,
        "electrical_cycle_mm": electrical_cycle_mm,
        "electrical_hz": electrical_hz,
        "samples_per_cycle": samples_per_cycle,
        "nyquist_harmonic_limit": nyquist_harmonic_limit,
        "selected_axis": selected_axis,
        "recommended_max_speed_mm_s": recommended_max_speed,
        "min_samples_per_cycle": opts.min_samples_per_cycle,
        "axes": analyses,
    }


def summarize_phase_bins(values_per_bin):
    summary = []
    for bin_index, values in enumerate(values_per_bin):
        if not values:
            continue
        mean_value = statistics.fmean(values)
        rms_value = math.sqrt(
            statistics.fmean([value * value for value in values]))
        summary.append({
            "bin": bin_index,
            "count": len(values),
            "mean": mean_value,
            "rms": rms_value,
            "min": min(values),
            "max": max(values),
        })
    return summary


def fill_missing_circular(values):
    known = [
        (index, value)
        for index, value in enumerate(values)
        if value is not None
    ]
    if not known:
        raise ValueError("Need at least one populated phase bin")
    if len(known) == 1:
        return [known[0][1]] * len(values)
    filled = list(values)
    total = len(values)
    for pair_index, (start_index, start_value) in enumerate(known):
        end_index, end_value = known[(pair_index + 1) % len(known)]
        span = (end_index - start_index) % total
        if span == 0:
            continue
        for offset in range(1, span):
            target_index = (start_index + offset) % total
            ratio = offset / span
            filled[target_index] = (
                start_value + ratio * (end_value - start_value))
    for index, value in enumerate(filled):
        if value is None:
            filled[index] = 0.0
    return filled


def interpolate_circular_bins(values, point_count):
    samples = []
    total_bins = len(values)
    for point_index in range(point_count):
        bin_pos = (point_index * total_bins) / point_count
        left_index = int(math.floor(bin_pos)) % total_bins
        right_index = (left_index + 1) % total_bins
        ratio = bin_pos - math.floor(bin_pos)
        samples.append(
            values[left_index]
            + ratio * (values[right_index] - values[left_index]))
    return samples


def clamp01(value):
    return max(0.0, min(1.0, value))


def circular_shift(values, shift):
    total = len(values)
    return [values[(index - shift) % total] for index in range(total)]


def circular_moving_average(values, window):
    if window <= 1 or len(values) <= 1:
        return list(values)
    if window % 2 == 0:
        window += 1
    radius = window // 2
    total = len(values)
    smoothed = []
    for index in range(total):
        samples = [
            values[(index + offset) % total]
            for offset in range(-radius, radius + 1)
        ]
        smoothed.append(statistics.fmean(samples))
    return smoothed


def best_circular_shift(reference, candidate):
    if len(reference) != len(candidate):
        raise ValueError("Reference and candidate must have the same length")
    best_shift = 0
    best_score = None
    for shift in range(len(reference)):
        shifted = circular_shift(candidate, shift)
        score = sum(a * b for a, b in zip(reference, shifted))
        if best_score is None or score > best_score:
            best_shift = shift
            best_score = score
    return best_shift, best_score if best_score is not None else 0.0


def best_circular_shift_local(reference, candidate, center_shift, radius):
    if len(reference) != len(candidate):
        raise ValueError("Reference and candidate must have the same length")
    total = len(reference)
    best_shift = center_shift % total
    best_score = None
    for delta in range(-radius, radius + 1):
        shift = (center_shift + delta) % total
        shifted = circular_shift(candidate, shift)
        score = sum(a * b for a, b in zip(reference, shifted))
        if best_score is None or score > best_score:
            best_shift = shift
            best_score = score
    return best_shift, best_score if best_score is not None else 0.0


def harmonic_descriptor(values, harmonic):
    total = len(values)
    cosine = 0.0
    sine = 0.0
    for index, value in enumerate(values):
        angle = 2.0 * math.pi * harmonic * index / total
        cosine += value * math.cos(angle)
        sine += value * math.sin(angle)
    cosine /= total
    sine /= total
    magnitude = math.sqrt(cosine * cosine + sine * sine)
    phase_deg = math.degrees(math.atan2(-sine, cosine))
    return {
        "harmonic": harmonic,
        "magnitude": magnitude,
        "phase_deg": phase_deg,
    }


def compute_phase_residual_quality(residual_norm, phase_bin_counts,
                                   samples_per_cycle, opts):
    inspected_harmonics = max(
        1, min(opts.phase_residual_quality_harmonics, len(residual_norm) // 2))
    harmonic_descriptors = [
        harmonic_descriptor(residual_norm, harmonic)
        for harmonic in range(1, inspected_harmonics + 1)
    ]
    ranked = sorted(
        harmonic_descriptors, key=lambda item: item["magnitude"], reverse=True)
    dominant = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    total_magnitude = sum(item["magnitude"] for item in ranked)
    populated_phase_fraction = (
        sum(1 for count in phase_bin_counts if count > 0) / len(phase_bin_counts))
    samples_per_cycle_score = clamp01(
        samples_per_cycle / opts.min_samples_per_cycle)
    harmonic_focus = (
        dominant["magnitude"] / total_magnitude
        if total_magnitude > 0.0 else 0.0)
    if second is None or second["magnitude"] <= 1.0e-12:
        harmonic_dominance_ratio = float("inf")
        harmonic_dominance_score = 1.0
    else:
        harmonic_dominance_ratio = (
            dominant["magnitude"] / second["magnitude"])
        # Prusa uses a magnitude quotient of 2.0 as a useful bound during
        # calibration. Reuse that here as the saturation point for the
        # normalized dominance score in the offline residual path.
        harmonic_dominance_score = clamp01(harmonic_dominance_ratio / 2.0)
    quality_score = (
        0.35 * samples_per_cycle_score
        + 0.25 * populated_phase_fraction
        + 0.20 * harmonic_focus
        + 0.20 * harmonic_dominance_score)
    return {
        "score": quality_score,
        "samples_per_cycle_score": samples_per_cycle_score,
        "populated_phase_fraction": populated_phase_fraction,
        "harmonic_focus": harmonic_focus,
        "harmonic_dominance_ratio": harmonic_dominance_ratio,
        "harmonic_dominance_score": harmonic_dominance_score,
        "dominant_harmonic": dominant["harmonic"],
        "dominant_harmonic_magnitude": dominant["magnitude"],
        "inspected_harmonics": harmonic_descriptors,
    }


def summarize_candidate_residual_harmonics(values, opts):
    inspected = max(
        1, min(opts.candidate_residual_harmonics, len(values) // 2))
    descriptors = [
        harmonic_descriptor(values, harmonic)
        for harmonic in range(1, inspected + 1)
    ]
    total_magnitude = sum(item["magnitude"] for item in descriptors)
    harmonic_items = []
    recommended = []
    for item in descriptors:
        magnitude_share = (
            item["magnitude"] / total_magnitude
            if total_magnitude > 0.0 else 0.0)
        summary = {
            "harmonic": item["harmonic"],
            "magnitude": item["magnitude"],
            "phase_deg": item["phase_deg"],
            "magnitude_share": magnitude_share,
            "recommended": magnitude_share >= opts.candidate_residual_share_threshold,
        }
        harmonic_items.append(summary)
        if summary["recommended"]:
            recommended.append(summary["harmonic"])
    return {
        "harmonics": harmonic_items,
        "recommended_harmonics": recommended,
        "share_threshold": opts.candidate_residual_share_threshold,
    }


def load_candidate_residual(path):
    with open(path) as handle:
        payload = json.load(handle)
    required = (
        "selected_axis", "phase_residual_points", "alignment_strategy",
        "dominant_harmonic_mode", "harmonic_summary", "norm_q15")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(
            "Candidate reference is missing required keys: %s" % (
                ",".join(missing),))
    return payload


def compare_candidate_residuals(candidate, reference, reference_path, opts):
    if candidate["phase_residual_points"] != reference["phase_residual_points"]:
        raise ValueError("Candidate/reference phase_residual_points mismatch")
    point_count = candidate["phase_residual_points"]
    candidate_q15 = candidate["norm_q15"]
    reference_q15 = reference["norm_q15"]
    if len(candidate_q15) != point_count or len(reference_q15) != point_count:
        raise ValueError("Candidate/reference q15 length mismatch")
    q15_delta = [
        cand - ref
        for cand, ref in zip(candidate_q15, reference_q15)
    ]
    mean_abs_q15_delta = statistics.fmean(abs(value) for value in q15_delta)
    max_abs_q15_delta = max(abs(value) for value in q15_delta)
    rms_q15_delta = math.sqrt(statistics.fmean(
        value * value for value in q15_delta))
    candidate_norm = candidate["norm_smoothed"]
    reference_norm = reference["norm_smoothed"]
    mean_abs_norm_delta = statistics.fmean(
        abs(cand - ref) for cand, ref in zip(candidate_norm, reference_norm))
    harmonic_map_ref = {
        item["harmonic"]: item
        for item in reference["harmonic_summary"]["harmonics"]
    }
    harmonic_comparison = []
    for item in candidate["harmonic_summary"]["harmonics"]:
        harmonic = item["harmonic"]
        ref_item = harmonic_map_ref.get(harmonic)
        if ref_item is None:
            continue
        harmonic_comparison.append({
            "harmonic": harmonic,
            "candidate_magnitude_share": item["magnitude_share"],
            "reference_magnitude_share": ref_item["magnitude_share"],
            "magnitude_share_delta": (
                item["magnitude_share"] - ref_item["magnitude_share"]),
            "candidate_phase_deg": item["phase_deg"],
            "reference_phase_deg": ref_item["phase_deg"],
            "phase_delta_deg": angle_delta_deg(
                item["phase_deg"], ref_item["phase_deg"]),
            "candidate_recommended": item["recommended"],
            "reference_recommended": ref_item["recommended"],
        })
    reference_recommended = set(
        reference["harmonic_summary"]["recommended_harmonics"])
    candidate_recommended = set(
        candidate["harmonic_summary"]["recommended_harmonics"])
    materially_shifted_harmonics = sorted(
        item["harmonic"]
        for item in harmonic_comparison
        if item["phase_delta_deg"]
        >= opts.candidate_reference_refresh_phase_delta_deg)
    recommendation_reasons = []
    if mean_abs_q15_delta >= opts.candidate_reference_refresh_q15_mean_abs:
        recommendation_reasons.append(
            "mean_abs_q15_delta>=%.3f" % (
                opts.candidate_reference_refresh_q15_mean_abs,))
    if candidate_recommended != reference_recommended:
        recommendation_reasons.append("recommended_harmonics_changed")
    if materially_shifted_harmonics:
        recommendation_reasons.append(
            "harmonic_phase_delta>=%gdeg(%s)" % (
                opts.candidate_reference_refresh_phase_delta_deg,
                ",".join("H%d" % (harmonic,)
                         for harmonic in materially_shifted_harmonics)))
    return {
        "reference_file": str(reference_path),
        "selected_axis_match": (
            candidate["selected_axis"] == reference["selected_axis"]),
        "selected_axis_candidate": candidate["selected_axis"],
        "selected_axis_reference": reference["selected_axis"],
        "phase_residual_points": point_count,
        "alignment_strategy_candidate": candidate["alignment_strategy"],
        "alignment_strategy_reference": reference["alignment_strategy"],
        "alignment_harmonic_lock_candidate": (
            candidate.get("alignment_harmonic_lock")),
        "alignment_harmonic_lock_reference": (
            reference.get("alignment_harmonic_lock")),
        "dominant_harmonic_mode_candidate": (
            candidate["dominant_harmonic_mode"]),
        "dominant_harmonic_mode_reference": (
            reference["dominant_harmonic_mode"]),
        "q15_delta_summary": {
            "mean_abs": mean_abs_q15_delta,
            "max_abs": max_abs_q15_delta,
            "rms": rms_q15_delta,
        },
        "norm_delta_summary": {
            "mean_abs": mean_abs_norm_delta,
        },
        "recommended_harmonics_candidate": sorted(candidate_recommended),
        "recommended_harmonics_reference": sorted(reference_recommended),
        "recommended_harmonics_overlap": sorted(
            candidate_recommended & reference_recommended),
        "materially_shifted_harmonics": materially_shifted_harmonics,
        "refresh_thresholds": {
            "mean_abs_q15_delta": (
                opts.candidate_reference_refresh_q15_mean_abs),
            "phase_delta_deg": (
                opts.candidate_reference_refresh_phase_delta_deg),
        },
        "refresh_recommended": bool(recommendation_reasons),
        "refresh_reasons": recommendation_reasons,
        "harmonics": harmonic_comparison,
        "q15_delta_preview": q15_delta[:16],
    }


def best_harmonic_alignment(reference, candidate, max_harmonic=4):
    descriptors = []
    for harmonic in range(1, max_harmonic + 1):
        ref_desc = harmonic_descriptor(reference, harmonic)
        cand_desc = harmonic_descriptor(candidate, harmonic)
        score = min(ref_desc["magnitude"], cand_desc["magnitude"])
        descriptors.append({
            "harmonic": harmonic,
            "score": score,
            "reference": ref_desc,
            "candidate": cand_desc,
        })
    best = max(descriptors, key=lambda item: item["score"])
    total = len(reference)
    phase_delta = (
        best["candidate"]["phase_deg"] - best["reference"]["phase_deg"])
    raw_shift = int(round(
        phase_delta * total / (360.0 * best["harmonic"])))
    return raw_shift % total, best


def specific_harmonic_alignment(reference, candidate, harmonic):
    descriptor = {
        "harmonic": harmonic,
        "reference": harmonic_descriptor(reference, harmonic),
        "candidate": harmonic_descriptor(candidate, harmonic),
    }
    descriptor["score"] = min(
        descriptor["reference"]["magnitude"],
        descriptor["candidate"]["magnitude"])
    total = len(reference)
    phase_delta = (
        descriptor["candidate"]["phase_deg"]
        - descriptor["reference"]["phase_deg"])
    raw_shift = int(round(
        phase_delta * total / (360.0 * descriptor["harmonic"])))
    return raw_shift % total, descriptor


def peak_alignment_shift(reference, candidate):
    total = len(reference)
    ref_index = max(range(total), key=lambda idx: abs(reference[idx]))
    cand_index = max(range(total), key=lambda idx: abs(candidate[idx]))
    return (ref_index - cand_index) % total, {
        "reference_peak_index": ref_index,
        "candidate_peak_index": cand_index,
    }


def align_residual_curve(reference, candidate, opts):
    mode = opts.phase_residual_align
    refine_radius = max(4, opts.phase_residual_smoothing_window * 2)
    if mode == "correlation":
        shift, score = best_circular_shift(reference, candidate)
        return {
            "shift": shift,
            "alignment_score": score,
            "strategy": "correlation",
            "details": {},
        }
    if mode == "peak":
        raw_shift, details = peak_alignment_shift(reference, candidate)
        shift, score = best_circular_shift_local(
            reference, candidate, raw_shift, refine_radius)
        return {
            "shift": shift,
            "alignment_score": score,
            "strategy": "peak+local_correlation",
            "details": details,
        }
    if mode == "h1":
        raw_shift, details = best_harmonic_alignment(reference, candidate, 1)
        shift, score = best_circular_shift_local(
            reference, candidate, raw_shift, refine_radius)
        return {
            "shift": shift,
            "alignment_score": score,
            "strategy": "h1+local_correlation",
            "details": details,
        }
    if mode == "harmonic":
        harmonic = max(1, opts.phase_residual_align_harmonic)
        raw_shift, details = specific_harmonic_alignment(
            reference, candidate, harmonic)
        shift, score = best_circular_shift_local(
            reference, candidate, raw_shift, refine_radius)
        return {
            "shift": shift,
            "alignment_score": score,
            "strategy": "harmonic%d+local_correlation" % (harmonic,),
            "details": details,
        }
    raw_shift, details = best_harmonic_alignment(reference, candidate, 4)
    shift, score = best_circular_shift_local(
        reference, candidate, raw_shift, refine_radius)
    return {
        "shift": shift,
        "alignment_score": score,
        "strategy": "auto_harmonic+local_correlation",
        "details": details,
    }


def build_phase_residual_export(result, opts):
    electrical_cycle_mm = (
        opts.rotation_distance
        / opts.full_steps_per_rotation * 4.0)
    electrical_hz = (
        opts.speed_mm_s / electrical_cycle_mm
        if electrical_cycle_mm > 0.0 else 0.0)
    phase_bin_means = [None] * result["phase_bins"]
    phase_bin_counts = [0] * result["phase_bins"]
    phase_bin_rms = [0.0] * result["phase_bins"]
    for item in result["phase_bin_summary"]:
        bin_index = item["bin"]
        phase_bin_means[bin_index] = item["mean"]
        phase_bin_counts[bin_index] = item["count"]
        phase_bin_rms[bin_index] = item["rms"]
    filled_means = fill_missing_circular(phase_bin_means)
    mean_offset = statistics.fmean(filled_means)
    centered_means = [value - mean_offset for value in filled_means]
    residual_curve = interpolate_circular_bins(
        centered_means, opts.phase_residual_points)
    peak_abs = max(abs(value) for value in residual_curve) if residual_curve else 0.0
    if peak_abs > 0.0:
        residual_norm = [value / peak_abs for value in residual_curve]
    else:
        residual_norm = [0.0] * len(residual_curve)
    residual_q15 = [
        int(round(max(-1.0, min(1.0, value)) * 32767.0))
        for value in residual_norm
    ]
    quality_samples_per_cycle = (
        result["interpolated_sample_count"]
        / (result["overlap_duration_s"] * electrical_hz)
        if result["overlap_duration_s"] > 0.0 and electrical_hz > 0.0
        else 0.0)
    residual_quality = compute_phase_residual_quality(
        residual_norm, phase_bin_counts, quality_samples_per_cycle, opts)
    return {
        "selected_axis": result["selected_axis"],
        "source_accel_file": result["accel_file"],
        "source_trace_file": result["trace_file"],
        "phase_bin_value_domain": result["phase_bin_value_domain"],
        "phase_bins": result["phase_bins"],
        "phase_residual_points": opts.phase_residual_points,
        "phase_bin_mean_centered": centered_means,
        "phase_bin_rms": phase_bin_rms,
        "phase_bin_counts": phase_bin_counts,
        "interpolated_residual": {
            "units": "selected_axis_detrended",
            "peak_abs": peak_abs,
            "mean_removed": mean_offset,
            "residual_curve": residual_curve,
            "residual_norm": residual_norm,
            "residual_q15": residual_q15,
        },
        "residual_harmonics": residual_quality["inspected_harmonics"],
        "quality": {
            "min_bin_count": min(phase_bin_counts),
            "max_bin_count": max(phase_bin_counts),
            "mean_bin_count": statistics.fmean(phase_bin_counts),
            "selected_axis_rms": result["selected_axis_rms"],
            "trace_step_rate_hz": result["trace_step_rate_hz"],
            "interpolated_sample_count": result["interpolated_sample_count"],
            "samples_per_cycle": quality_samples_per_cycle,
            "quality_score": residual_quality["score"],
            "samples_per_cycle_score": residual_quality["samples_per_cycle_score"],
            "populated_phase_fraction": residual_quality["populated_phase_fraction"],
            "harmonic_focus": residual_quality["harmonic_focus"],
            "harmonic_dominance_ratio": residual_quality["harmonic_dominance_ratio"],
            "harmonic_dominance_score": residual_quality["harmonic_dominance_score"],
            "dominant_harmonic": residual_quality["dominant_harmonic"],
            "dominant_harmonic_magnitude": (
                residual_quality["dominant_harmonic_magnitude"]),
        },
        "notes": (
            "Prototype residual curve derived from one paired accel/trace "
            "capture. It is suitable for offline comparison and later profile "
            "construction, not yet as a validated runtime correction table."
        ),
    }


def build_phase_residual_aggregate(items, opts):
    if not items:
        raise ValueError("Need at least one phase residual export to aggregate")
    first = items[0]
    selected_axis = first["selected_axis"]
    phase_bins = first["phase_bins"]
    phase_points = first["phase_residual_points"]
    for item in items[1:]:
        if item["selected_axis"] != selected_axis:
            raise ValueError(
                "Cannot aggregate mixed selected axes (%s vs %s)" % (
                    selected_axis, item["selected_axis"]))
        if item["phase_bins"] != phase_bins:
            raise ValueError("Cannot aggregate mismatched phase bin counts")
        if item["phase_residual_points"] != phase_points:
            raise ValueError("Cannot aggregate mismatched phase point counts")
    accepted = []
    rejected = []
    for item in items:
        reasons = []
        if item["quality"]["samples_per_cycle"] < opts.min_samples_per_cycle:
            reasons.append(
                "samples_per_cycle<%.3f" % (opts.min_samples_per_cycle,))
        if item["quality"]["min_bin_count"] < opts.phase_residual_min_bin_count:
            reasons.append(
                "min_bin_count<%d" % (opts.phase_residual_min_bin_count,))
        if item["quality"]["quality_score"] < opts.phase_residual_quality_threshold:
            reasons.append(
                "quality_score<%.3f" % (
                    opts.phase_residual_quality_threshold,))
        if reasons:
            rejected.append({
                "accel_file": item["source_accel_file"],
                "trace_file": item["source_trace_file"],
                "reasons": reasons,
                "samples_per_cycle": item["quality"]["samples_per_cycle"],
                "min_bin_count": item["quality"]["min_bin_count"],
                "quality_score": item["quality"]["quality_score"],
                "dominant_harmonic": item["quality"]["dominant_harmonic"],
            })
            continue
        accepted.append(item)
    if not accepted:
        raise ValueError("No residual captures passed the aggregation filters")
    reference = max(
        accepted,
        key=lambda item: (
            item["quality"]["quality_score"],
            item["quality"]["min_bin_count"],
            item["quality"]["mean_bin_count"],
            item["quality"]["samples_per_cycle"]))
    smoothing_window = opts.phase_residual_smoothing_window
    reference_norm = circular_moving_average(
        reference["interpolated_residual"]["residual_norm"],
        smoothing_window)
    aligned_items = []
    for item in accepted:
        candidate_norm = circular_moving_average(
            item["interpolated_residual"]["residual_norm"],
            smoothing_window)
        alignment = align_residual_curve(reference_norm, candidate_norm, opts)
        aligned_items.append({
            "item": item,
            "shift": alignment["shift"],
            "alignment_score": alignment["alignment_score"],
            "alignment_strategy": alignment["strategy"],
            "alignment_details": alignment["details"],
            "curve": circular_shift(
                item["interpolated_residual"]["residual_curve"],
                alignment["shift"]),
            "norm": circular_shift(
                item["interpolated_residual"]["residual_norm"],
                alignment["shift"]),
        })
    curve_rows = [entry["curve"] for entry in aligned_items]
    norm_rows = [entry["norm"] for entry in aligned_items]
    curve_mean = []
    curve_stdev = []
    curve_min = []
    curve_max = []
    norm_mean = []
    norm_stdev = []
    for point_index in range(phase_points):
        curve_values = [row[point_index] for row in curve_rows]
        norm_values = [row[point_index] for row in norm_rows]
        curve_mean.append(statistics.fmean(curve_values))
        curve_stdev.append(
            statistics.stdev(curve_values) if len(curve_values) > 1 else 0.0)
        curve_min.append(min(curve_values))
        curve_max.append(max(curve_values))
        norm_mean.append(statistics.fmean(norm_values))
        norm_stdev.append(
            statistics.stdev(norm_values) if len(norm_values) > 1 else 0.0)
    norm_mean_smoothed = circular_moving_average(norm_mean, smoothing_window)
    curve_mean_smoothed = circular_moving_average(curve_mean, smoothing_window)
    residual_q15 = [
        int(round(max(-1.0, min(1.0, value)) * 32767.0))
        for value in norm_mean_smoothed
    ]
    candidate_harmonics = summarize_candidate_residual_harmonics(
        norm_mean_smoothed, opts)
    candidate_residual = {
        "selected_axis": selected_axis,
        "phase_residual_points": phase_points,
        "accepted_capture_count": len(aligned_items),
        "rejected_capture_count": len(rejected),
        "alignment_strategy": opts.phase_residual_align,
        "alignment_harmonic_lock": (
            opts.phase_residual_align_harmonic
            if opts.phase_residual_align == "harmonic" else None),
        "dominant_harmonic_mode": statistics.multimode([
            entry["item"]["quality"]["dominant_harmonic"]
            for entry in aligned_items])[0],
        "quality_summary": {
            "samples_per_cycle_mean": statistics.fmean(
                entry["item"]["quality"]["samples_per_cycle"]
                for entry in aligned_items),
            "quality_score_mean": statistics.fmean(
                entry["item"]["quality"]["quality_score"]
                for entry in aligned_items),
            "quality_score_min": min(
                entry["item"]["quality"]["quality_score"]
                for entry in aligned_items),
            "alignment_score_mean": statistics.fmean(
                entry["alignment_score"] for entry in aligned_items),
            "smoothing_window": smoothing_window,
        },
        "accepted_accel_files": [
            Path(entry["item"]["source_accel_file"]).name
            for entry in aligned_items
        ],
        "rejected_accel_files": [
            Path(item["accel_file"]).name
            for item in rejected
        ],
        "harmonic_summary": candidate_harmonics,
        "units": "selected_axis_detrended",
        "curve_smoothed": curve_mean_smoothed,
        "norm_smoothed": norm_mean_smoothed,
        "norm_q15": residual_q15,
        "notes": (
            "First smoothed candidate residual built from the accepted "
            "synchronized captures at one working point. This remains an "
            "offline prototype, not a runtime-ready correction table."
        ),
    }
    return {
        "selected_axis": selected_axis,
        "phase_bins": phase_bins,
        "phase_residual_points": phase_points,
        "capture_count": len(items),
        "accepted_capture_count": len(aligned_items),
        "rejected_capture_count": len(rejected),
        "source_pairs": [
            {
                "accel_file": item["source_accel_file"],
                "trace_file": item["source_trace_file"],
            }
            for item in items
        ],
        "accepted_pairs": [
            {
                "accel_file": entry["item"]["source_accel_file"],
                "trace_file": entry["item"]["source_trace_file"],
                "shift": entry["shift"],
                "alignment_score": entry["alignment_score"],
                "alignment_strategy": entry["alignment_strategy"],
                "alignment_details": entry["alignment_details"],
                "samples_per_cycle": entry["item"]["quality"]["samples_per_cycle"],
                "min_bin_count": entry["item"]["quality"]["min_bin_count"],
                "quality_score": entry["item"]["quality"]["quality_score"],
                "populated_phase_fraction": (
                    entry["item"]["quality"]["populated_phase_fraction"]),
                "dominant_harmonic": entry["item"]["quality"]["dominant_harmonic"],
                "harmonic_focus": entry["item"]["quality"]["harmonic_focus"],
                "harmonic_dominance_ratio": (
                    entry["item"]["quality"]["harmonic_dominance_ratio"]),
            }
            for entry in aligned_items
        ],
        "rejected_pairs": rejected,
        "aggregated_curve": {
            "units": "selected_axis_detrended",
            "curve_mean": curve_mean,
            "curve_mean_smoothed": curve_mean_smoothed,
            "curve_stdev": curve_stdev,
            "curve_min": curve_min,
            "curve_max": curve_max,
            "norm_mean": norm_mean,
            "norm_mean_smoothed": norm_mean_smoothed,
            "norm_stdev": norm_stdev,
            "norm_q15": residual_q15,
        },
        "candidate_residual": candidate_residual,
        "quality": {
            "mean_peak_abs": statistics.fmean(
                entry["item"]["interpolated_residual"]["peak_abs"]
                for entry in aligned_items),
            "mean_bin_count": statistics.fmean(
                entry["item"]["quality"]["mean_bin_count"]
                for entry in aligned_items),
            "min_bin_count": min(
                entry["item"]["quality"]["min_bin_count"]
                for entry in aligned_items),
            "max_bin_count": max(
                entry["item"]["quality"]["max_bin_count"]
                for entry in aligned_items),
            "trace_step_rate_hz_mean": statistics.fmean(
                entry["item"]["quality"]["trace_step_rate_hz"]
                for entry in aligned_items),
            "samples_per_cycle_mean": statistics.fmean(
                entry["item"]["quality"]["samples_per_cycle"]
                for entry in aligned_items),
            "quality_score_mean": statistics.fmean(
                entry["item"]["quality"]["quality_score"]
                for entry in aligned_items),
            "quality_score_min": min(
                entry["item"]["quality"]["quality_score"]
                for entry in aligned_items),
            "quality_score_threshold": opts.phase_residual_quality_threshold,
            "dominant_harmonic_mode": statistics.multimode([
                entry["item"]["quality"]["dominant_harmonic"]
                for entry in aligned_items])[0],
            "alignment_score_mean": statistics.fmean(
                entry["alignment_score"] for entry in aligned_items),
            "alignment_shift_span": {
                "min": min(entry["shift"] for entry in aligned_items),
                "max": max(entry["shift"] for entry in aligned_items),
            },
            "alignment_strategy": opts.phase_residual_align,
            "smoothing_window": smoothing_window,
        },
        "notes": (
            "Prototype aggregate residual curve across repeated synchronized "
            "captures at one working point. The aggregate now filters weak "
            "runs, aligns accepted curves phase-wise, and uses circular "
            "moving-average smoothing before exporting the normalized q15 "
            "preview."
        ),
    }


def analyze_paired_capture(accel_path, trace_path, opts):
    accel_times, accel_axes = load_csv(accel_path)
    trace_rows = load_trace_csv(trace_path)
    trace_times = [row["step_time"] for row in trace_rows]
    overlap_start = max(accel_times[0], trace_times[0])
    overlap_end = min(accel_times[-1], trace_times[-1])
    if overlap_end <= overlap_start:
        raise ValueError("No overlap between accel and trace windows")
    selected_axis = opts.axis
    if selected_axis == "auto":
        accel_analysis = {
            axis: analyze_axis(accel_times, accel_axes[axis],
                               opts.speed_mm_s / (
                                   opts.rotation_distance
                                   / opts.full_steps_per_rotation * 4.0),
                               opts.harmonics)
            for axis in AXES
        }
        selected_axis = max(
            AXES, key=lambda axis: accel_analysis[axis]["harmonics"][0]["magnitude"])
    interpolated = []
    nearest_offsets = []
    for accel_index, accel_time in enumerate(accel_times):
        if accel_time < overlap_start or accel_time > overlap_end:
            continue
        right = bisect.bisect_right(trace_times, accel_time)
        if right == 0 or right >= len(trace_rows):
            continue
        left = right - 1
        left_row = trace_rows[left]
        right_row = trace_rows[right]
        left_time = left_row["step_time"]
        right_time = right_row["step_time"]
        if right_time <= left_time:
            continue
        weight = (accel_time - left_time) / (right_time - left_time)
        phase_delta = (
            (right_row["phase_index"] - left_row["phase_index"]) % 1024)
        if phase_delta > 512:
            phase_delta -= 1024
        phase_index = (left_row["phase_index"] + weight * phase_delta) % 1024
        commanded_position_mm = (
            left_row["commanded_position_mm"]
            + weight * (
                right_row["commanded_position_mm"]
                - left_row["commanded_position_mm"]))
        nearest_offsets.append(min(
            abs(accel_time - left_time), abs(right_time - accel_time)))
        interpolated.append({
            "time": accel_time,
            "phase_index": phase_index,
            "commanded_position_mm": commanded_position_mm,
            "accel_x": accel_axes["accel_x"][accel_index],
            "accel_y": accel_axes["accel_y"][accel_index],
            "accel_z": accel_axes["accel_z"][accel_index],
        })
    if len(interpolated) < 3:
        raise ValueError("Need at least 3 overlapping accel/trace samples")
    trace_duration = trace_times[-1] - trace_times[0]
    trace_step_rate_hz = ((len(trace_times) - 1) / trace_duration
                          if trace_duration > 0.0 else 0.0)
    values = [row[selected_axis] for row in interpolated]
    mean_value = statistics.fmean(values)
    detrended = [value - mean_value for value in values]
    phase_bin_size = 1024.0 / opts.phase_bins
    phase_bins = [[] for _ in range(opts.phase_bins)]
    for row, detrended_value in zip(interpolated, detrended):
        bin_index = min(
            opts.phase_bins - 1,
            int(row["phase_index"] / phase_bin_size))
        phase_bins[bin_index].append(detrended_value)
    phase_bin_summary = summarize_phase_bins(phase_bins)
    accel_harmonics = analyze_axis(
        [row["time"] for row in interpolated],
        [row[selected_axis] for row in interpolated],
        opts.speed_mm_s / (
            opts.rotation_distance / opts.full_steps_per_rotation * 4.0),
        opts.harmonics)
    return {
        "accel_file": str(accel_path),
        "trace_file": str(trace_path),
        "selected_axis": selected_axis,
        "phase_bins": opts.phase_bins,
        "overlap_start_s": overlap_start,
        "overlap_end_s": overlap_end,
        "overlap_duration_s": overlap_end - overlap_start,
        "interpolated_sample_count": len(interpolated),
        "trace_sample_count": len(trace_rows),
        "trace_step_rate_hz": trace_step_rate_hz,
        "nearest_trace_offset_s": summarize_numeric(nearest_offsets),
        "phase_bin_summary": phase_bin_summary,
        "phase_bin_value_domain": "selected_axis_detrended",
        "selected_axis_mean": mean_value,
        "selected_axis_rms": math.sqrt(
            statistics.fmean([value * value for value in detrended])),
        "time_domain_harmonics": accel_harmonics["harmonics"],
    }


def angle_delta_deg(deg_a, deg_b):
    delta = (deg_a - deg_b + 180.0) % 360.0 - 180.0
    return abs(delta)


def circular_mean_deg(angles_deg):
    if not angles_deg:
        return None
    sin_sum = sum(math.sin(math.radians(angle)) for angle in angles_deg)
    cos_sum = sum(math.cos(math.radians(angle)) for angle in angles_deg)
    if abs(sin_sum) < 1e-12 and abs(cos_sum) < 1e-12:
        return 0.0
    return math.degrees(math.atan2(sin_sum, cos_sum))


def summarize_numeric(values):
    return {
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "count": len(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def aggregate_forward_backward(results, opts):
    grouped = defaultdict(lambda: defaultdict(list))
    for result in results:
        meta = result.get("filename_meta", {})
        direction = meta.get("direction")
        speed = meta.get("speed_mm_s")
        if direction is None or speed is None:
            continue
        grouped[speed][direction].append(result)
    aggregated = []
    for speed in sorted(grouped):
        per_speed = grouped[speed]
        if "forward" not in per_speed or "backward" not in per_speed:
            continue
        selected_axis = opts.axis
        if selected_axis == "auto":
            axis_scores = {}
            for axis in AXES:
                forward_mean = statistics.fmean(
                    item["axes"][axis]["harmonics"][0]["magnitude"]
                    for item in per_speed["forward"])
                backward_mean = statistics.fmean(
                    item["axes"][axis]["harmonics"][0]["magnitude"]
                    for item in per_speed["backward"])
                axis_scores[axis] = forward_mean + backward_mean
            selected_axis = max(axis_scores, key=axis_scores.get)
        directions = {}
        for direction, items in per_speed.items():
            direction_summary = {
                "count": len(items),
                "files": [item["file"] for item in items],
                "sample_rate_hz": summarize_numeric(
                    [item["sample_rate_hz"] for item in items]),
                "samples_per_cycle": summarize_numeric(
                    [item["samples_per_cycle"] for item in items]),
                "harmonics": [],
            }
            for harmonic_index in range(opts.harmonics):
                harmonic_number = harmonic_index + 1
                harmonics = [
                    item["axes"][selected_axis]["harmonics"][harmonic_index]
                    for item in items
                ]
                direction_summary["harmonics"].append({
                    "harmonic": harmonic_number,
                    "frequency_hz": harmonics[0]["frequency_hz"],
                    "magnitude": summarize_numeric(
                        [item["magnitude"] for item in harmonics]),
                    "phase_deg": {
                        "mean": circular_mean_deg(
                            [item["phase_deg"] for item in harmonics]),
                        "count": len(harmonics),
                    },
                })
            directions[direction] = direction_summary
        harmonic_comparisons = []
        for harmonic_index in range(opts.harmonics):
            harmonic_number = harmonic_index + 1
            forward_h = directions["forward"]["harmonics"][harmonic_index]
            backward_h = directions["backward"]["harmonics"][harmonic_index]
            forward_mag = forward_h["magnitude"]["mean"]
            backward_mag = backward_h["magnitude"]["mean"]
            bigger = max(forward_mag, backward_mag)
            smaller = min(forward_mag, backward_mag)
            ratio = bigger / smaller if smaller > 0.0 else None
            harmonic_comparisons.append({
                "harmonic": harmonic_number,
                "forward_magnitude_mean": forward_mag,
                "backward_magnitude_mean": backward_mag,
                "forward_magnitude_stdev": forward_h["magnitude"]["stdev"],
                "backward_magnitude_stdev": backward_h["magnitude"]["stdev"],
                "magnitude_ratio": ratio,
                "phase_delta_deg": angle_delta_deg(
                    forward_h["phase_deg"]["mean"],
                    backward_h["phase_deg"]["mean"]),
            })
        aggregated.append({
            "speed_mm_s": speed,
            "selected_axis": selected_axis,
            "forward": directions["forward"],
            "backward": directions["backward"],
            "h1_magnitude_ratio": harmonic_comparisons[0]["magnitude_ratio"],
            "h1_phase_delta_deg": harmonic_comparisons[0]["phase_delta_deg"],
            "harmonics": harmonic_comparisons,
        })
    return aggregated


def compare_forward_backward(results, opts):
    grouped = {}
    for result in results:
        meta = result.get("filename_meta", {})
        direction = meta.get("direction")
        speed = meta.get("speed_mm_s")
        if direction is None or speed is None:
            continue
        grouped.setdefault(speed, {})[direction] = result
    comparisons = []
    for speed in sorted(grouped):
        pair = grouped[speed]
        if "forward" not in pair or "backward" not in pair:
            continue
        selected_axis = opts.axis
        if selected_axis == "auto":
            forward_axis = pair["forward"]["selected_axis"]
            backward_axis = pair["backward"]["selected_axis"]
            if forward_axis == backward_axis:
                selected_axis = forward_axis
            else:
                selected_axis = max(
                    AXES,
                    key=lambda axis: (
                        pair["forward"]["axes"][axis]["harmonics"][0]["magnitude"]
                        + pair["backward"]["axes"][axis]["harmonics"][0]["magnitude"]))
        harmonic_comparisons = []
        for harmonic in range(1, opts.harmonics + 1):
            forward_h = pair["forward"]["axes"][selected_axis]["harmonics"][
                harmonic - 1]
            backward_h = pair["backward"]["axes"][selected_axis]["harmonics"][
                harmonic - 1]
            ratio = None
            if forward_h["magnitude"] and backward_h["magnitude"]:
                bigger = max(forward_h["magnitude"], backward_h["magnitude"])
                smaller = min(forward_h["magnitude"], backward_h["magnitude"])
                ratio = bigger / smaller if smaller > 0.0 else None
            harmonic_comparisons.append({
                "harmonic": harmonic,
                "forward_magnitude": forward_h["magnitude"],
                "backward_magnitude": backward_h["magnitude"],
                "magnitude_ratio": ratio,
                "phase_delta_deg": angle_delta_deg(
                    forward_h["phase_deg"], backward_h["phase_deg"]),
            })
        h1_ratio = harmonic_comparisons[0]["magnitude_ratio"]
        comparisons.append({
            "speed_mm_s": speed,
            "selected_axis": selected_axis,
            "forward_file": pair["forward"]["file"],
            "backward_file": pair["backward"]["file"],
            "forward_samples_per_cycle": pair["forward"]["samples_per_cycle"],
            "backward_samples_per_cycle": pair["backward"]["samples_per_cycle"],
            "h1_magnitude_ratio": h1_ratio,
            "h1_phase_delta_deg": harmonic_comparisons[0]["phase_delta_deg"],
            "harmonics": harmonic_comparisons,
        })
    return comparisons


def format_text_report(result):
    selected = result["selected_axis"]
    lines = [
        "file: %s" % (result["file"],),
        "raw_samples: %d" % (result["raw_sample_count"],),
        "raw_duration_s: %.6f" % (result["raw_duration_s"],),
        "trim_mode: %s" % (result["trim_mode"],),
        "samples: %d" % (result["sample_count"],),
        "duration_s: %.6f" % (result["duration_s"],),
        "sample_rate_hz: %.3f" % (result["sample_rate_hz"],),
        "median_dt_s: %.6f" % (result["median_dt_s"],),
        "trim_start_index: %d" % (result["trim_start_index"],),
        "trim_end_index: %d" % (result["trim_end_index"],),
        "trim_start_time_s: %.6f" % (result["trim_start_time_s"],),
        "trim_end_time_s: %.6f" % (result["trim_end_time_s"],),
        "electrical_cycle_mm: %.6f" % (result["electrical_cycle_mm"],),
        "electrical_hz: %.3f" % (result["electrical_hz"],),
        "samples_per_cycle: %.3f" % (result["samples_per_cycle"],),
        "nyquist_harmonic_limit: %.3f" % (result["nyquist_harmonic_limit"],),
        "selected_axis: %s" % (selected,),
        "recommended_max_speed_mm_s@%.1f_spc: %.3f" % (
            result["min_samples_per_cycle"],
            result["recommended_max_speed_mm_s"]),
    ]
    if result["trim_mode"] == "auto":
        trim = result["trim_info"]
        if "threshold" in trim:
            lines.append(
                "trim_threshold: baseline=%.3f peak=%.3f threshold=%.3f" % (
                    trim["baseline"], trim["peak"], trim["threshold"]))
        elif "window_samples" in trim:
            if "best_score" in trim:
                lines.append(
                    "trim_expected_window: baseline=%.3f median_dt=%.6f "
                    "window_samples=%d best_score=%.3f" % (
                        trim["baseline"], trim["median_dt"],
                        trim["window_samples"], trim["best_score"]))
            else:
                lines.append(
                    "trim_expected_window: baseline=%.3f median_dt=%.6f "
                    "window_samples=%d" % (
                        trim["baseline"], trim["median_dt"],
                        trim["window_samples"]))
    for axis in AXES:
        axis_data = result["axes"][axis]
        axis_label = "%s%s" % (
            axis, " [selected]" if axis == selected else "")
        lines.append("%s:" % (axis_label,))
        lines.append("  mean=%.6f rms=%.6f" % (
            axis_data["mean"], axis_data["rms"]))
        for harmonic in axis_data["harmonics"]:
            lines.append(
                "  H%d freq=%.3fHz magnitude=%.6f phase_deg=%.3f" % (
                    harmonic["harmonic"], harmonic["frequency_hz"],
                    harmonic["magnitude"], harmonic["phase_deg"]))
    if result["samples_per_cycle"] < result["min_samples_per_cycle"]:
        lines.append(
            "warning: samples_per_cycle is below the configured target")
    if result["nyquist_harmonic_limit"] < 3.0:
        lines.append(
            "warning: sample rate only supports fewer than 3 electrical harmonics")
    return "\n".join(lines)


def format_compare_report(comparisons):
    lines = []
    for item in comparisons:
        lines.append("speed_mm_s: %.3f" % (item["speed_mm_s"],))
        lines.append("selected_axis: %s" % (item["selected_axis"],))
        lines.append(
            "samples_per_cycle: forward=%.3f backward=%.3f" % (
                item["forward_samples_per_cycle"],
                item["backward_samples_per_cycle"]))
        ratio = item["h1_magnitude_ratio"]
        ratio_text = "n/a" if ratio is None else "%.3f" % (ratio,)
        lines.append(
            "H1: magnitude_ratio=%s phase_delta_deg=%.3f" % (
                ratio_text, item["h1_phase_delta_deg"]))
        for harmonic in item["harmonics"]:
            ratio = harmonic["magnitude_ratio"]
            ratio_text = "n/a" if ratio is None else "%.3f" % (ratio,)
            lines.append(
                "  H%d: fwd=%.6f bck=%.6f ratio=%s phase_delta_deg=%.3f" % (
                    harmonic["harmonic"],
                    harmonic["forward_magnitude"],
                    harmonic["backward_magnitude"],
                    ratio_text,
                    harmonic["phase_delta_deg"]))
        lines.append(
            "files: forward=%s backward=%s" % (
                item["forward_file"], item["backward_file"]))
    return "\n".join(lines).rstrip()


def format_aggregate_report(aggregates):
    lines = []
    for item in aggregates:
        lines.append("aggregate_speed_mm_s: %.3f" % (item["speed_mm_s"],))
        lines.append("selected_axis: %s" % (item["selected_axis"],))
        lines.append(
            "runs: forward=%d backward=%d" % (
                item["forward"]["count"], item["backward"]["count"]))
        lines.append(
            "sample_rate_hz_mean: forward=%.3f backward=%.3f" % (
                item["forward"]["sample_rate_hz"]["mean"],
                item["backward"]["sample_rate_hz"]["mean"]))
        lines.append(
            "samples_per_cycle_mean: forward=%.3f backward=%.3f" % (
                item["forward"]["samples_per_cycle"]["mean"],
                item["backward"]["samples_per_cycle"]["mean"]))
        ratio = item["h1_magnitude_ratio"]
        ratio_text = "n/a" if ratio is None else "%.3f" % (ratio,)
        lines.append(
            "H1 aggregate: magnitude_ratio=%s phase_delta_deg=%.3f" % (
                ratio_text, item["h1_phase_delta_deg"]))
        for harmonic in item["harmonics"]:
            ratio = harmonic["magnitude_ratio"]
            ratio_text = "n/a" if ratio is None else "%.3f" % (ratio,)
            lines.append(
                "  H%d: fwd_mean=%.6f bck_mean=%.6f "
                "fwd_stdev=%.6f bck_stdev=%.6f ratio=%s phase_delta_deg=%.3f" % (
                    harmonic["harmonic"],
                    harmonic["forward_magnitude_mean"],
                    harmonic["backward_magnitude_mean"],
                    harmonic["forward_magnitude_stdev"],
                    harmonic["backward_magnitude_stdev"],
                    ratio_text,
                    harmonic["phase_delta_deg"]))
    return "\n".join(lines).rstrip()


def build_harmonic_basis(aggregates, opts):
    basis_items = []
    for item in aggregates:
        chosen = []
        for harmonic in item["harmonics"]:
            if harmonic["harmonic"] > opts.basis_harmonics:
                break
            ratio = harmonic["magnitude_ratio"]
            stable = ratio is not None and ratio <= opts.basis_ratio_threshold
            chosen.append({
                "harmonic": harmonic["harmonic"],
                "stable": stable,
                "magnitude_ratio": ratio,
                "phase_delta_deg": harmonic["phase_delta_deg"],
                "forward": {
                    "magnitude_mean": harmonic["forward_magnitude_mean"],
                    "magnitude_stdev": harmonic["forward_magnitude_stdev"],
                    "phase_deg_mean": item["forward"]["harmonics"][
                        harmonic["harmonic"] - 1]["phase_deg"]["mean"],
                },
                "backward": {
                    "magnitude_mean": harmonic["backward_magnitude_mean"],
                    "magnitude_stdev": harmonic["backward_magnitude_stdev"],
                    "phase_deg_mean": item["backward"]["harmonics"][
                        harmonic["harmonic"] - 1]["phase_deg"]["mean"],
                },
            })
        stable_harmonics = [
            entry["harmonic"] for entry in chosen if entry["stable"]
        ]
        basis_items.append({
            "speed_mm_s": item["speed_mm_s"],
            "selected_axis": item["selected_axis"],
            "basis_harmonics_limit": opts.basis_harmonics,
            "basis_ratio_threshold": opts.basis_ratio_threshold,
            "forward_runs": item["forward"]["count"],
            "backward_runs": item["backward"]["count"],
            "forward_sample_rate_hz_mean": item["forward"]["sample_rate_hz"]["mean"],
            "backward_sample_rate_hz_mean": item["backward"]["sample_rate_hz"]["mean"],
            "forward_samples_per_cycle_mean": (
                item["forward"]["samples_per_cycle"]["mean"]),
            "backward_samples_per_cycle_mean": (
                item["backward"]["samples_per_cycle"]["mean"]),
            "stable_harmonics": stable_harmonics,
            "recommended_harmonic_count": len(stable_harmonics),
            "harmonics": chosen,
        })
    return basis_items


def format_basis_report(basis_items):
    lines = []
    for item in basis_items:
        lines.append("basis_speed_mm_s: %.3f" % (item["speed_mm_s"],))
        lines.append("selected_axis: %s" % (item["selected_axis"],))
        lines.append(
            "runs: forward=%d backward=%d" % (
                item["forward_runs"], item["backward_runs"]))
        lines.append(
            "sample_rate_hz_mean: forward=%.3f backward=%.3f" % (
                item["forward_sample_rate_hz_mean"],
                item["backward_sample_rate_hz_mean"]))
        lines.append(
            "samples_per_cycle_mean: forward=%.3f backward=%.3f" % (
                item["forward_samples_per_cycle_mean"],
                item["backward_samples_per_cycle_mean"]))
        lines.append(
            "stable_harmonics@ratio<=%.3f: %s" % (
                item["basis_ratio_threshold"],
                ",".join(str(h) for h in item["stable_harmonics"]) or "none"))
        lines.append(
            "recommended_harmonic_count: %d" % (
                item["recommended_harmonic_count"],))
        for harmonic in item["harmonics"]:
            ratio = harmonic["magnitude_ratio"]
            ratio_text = "n/a" if ratio is None else "%.3f" % (ratio,)
            lines.append(
                "  H%d: stable=%d ratio=%s phase_delta_deg=%.3f "
                "fwd_mean=%.6f fwd_stdev=%.6f fwd_phase=%.3f "
                "bck_mean=%.6f bck_stdev=%.6f bck_phase=%.3f" % (
                    harmonic["harmonic"],
                    1 if harmonic["stable"] else 0,
                    ratio_text,
                    harmonic["phase_delta_deg"],
                    harmonic["forward"]["magnitude_mean"],
                    harmonic["forward"]["magnitude_stdev"],
                    harmonic["forward"]["phase_deg_mean"],
                    harmonic["backward"]["magnitude_mean"],
                    harmonic["backward"]["magnitude_stdev"],
                    harmonic["backward"]["phase_deg_mean"]))
    return "\n".join(lines).rstrip()


def synthesize_waveform(harmonics, point_count):
    values = []
    for index in range(point_count):
        electrical_phase = 2.0 * math.pi * index / point_count
        value = 0.0
        for harmonic in harmonics:
            amplitude = harmonic["magnitude_mean"]
            phase_rad = math.radians(harmonic["phase_deg_mean"])
            value += amplitude * math.cos(
                harmonic["harmonic"] * electrical_phase + phase_rad)
        values.append(value)
    peak = max((abs(value) for value in values), default=0.0)
    if peak > 0.0:
        normalized = [value / peak for value in values]
    else:
        normalized = [0.0 for _ in values]
    return {
        "raw": values,
        "normalized": normalized,
        "peak_abs_raw": peak,
        "mean_raw": statistics.fmean(values) if values else 0.0,
        "rms_raw": (
            math.sqrt(statistics.fmean([value * value for value in values]))
            if values else 0.0),
    }


def quantize_q15(values):
    quantized = []
    for value in values:
        clipped = max(-1.0, min(1.0, value))
        quantized.append(int(round(clipped * 32767.0)))
    return quantized


def build_direct_coil_profile(phase_offset_norm, phase_points, shift_scale_deg):
    coil_a = []
    coil_b = []
    for phase_index in range(phase_points):
        base_phase = 2.0 * math.pi * phase_index / phase_points
        normalized_shift = phase_offset_norm[phase_index]
        corrected_phase = base_phase + math.radians(
            shift_scale_deg * normalized_shift)
        coil_a.append(math.sin(corrected_phase))
        coil_b.append(math.cos(corrected_phase))
    return {
        "profile_kind": "u1_direct_mode_coil_unit_v1",
        "phase_points": phase_points,
        "shift_scale_deg_default": shift_scale_deg,
        "coil_a_unit_norm": coil_a,
        "coil_b_unit_norm": coil_b,
        "coil_a_unit_q15": quantize_q15(coil_a),
        "coil_b_unit_q15": quantize_q15(coil_b),
    }


def build_fit_export(basis_items, opts):
    fit_items = []
    for item in basis_items:
        directions = {}
        for direction in ("forward", "backward"):
            selected_harmonics = []
            for harmonic in item["harmonics"]:
                if not harmonic["stable"]:
                    continue
                direction_data = harmonic[direction]
                selected_harmonics.append({
                    "harmonic": harmonic["harmonic"],
                    "magnitude_mean": direction_data["magnitude_mean"],
                    "magnitude_stdev": direction_data["magnitude_stdev"],
                    "phase_deg_mean": direction_data["phase_deg_mean"],
                })
            directions[direction] = {
                "harmonics": selected_harmonics,
                "waveform": synthesize_waveform(
                    selected_harmonics, opts.fit_points),
            }
        fit_items.append({
            "speed_mm_s": item["speed_mm_s"],
            "selected_axis": item["selected_axis"],
            "prototype_only": True,
            "notes": (
                "Derived from accelerometer-domain harmonics; normalized shape "
                "only, not yet a validated direct-mode current table."),
            "fit_points": opts.fit_points,
            "stable_harmonics": item["stable_harmonics"],
            "recommended_harmonic_count": item["recommended_harmonic_count"],
            "directions": directions,
        })
    return fit_items


def format_fit_report(fit_items):
    lines = []
    for item in fit_items:
        lines.append("fit_speed_mm_s: %.3f" % (item["speed_mm_s"],))
        lines.append("selected_axis: %s" % (item["selected_axis"],))
        lines.append(
            "stable_harmonics: %s" % (
                ",".join(str(h) for h in item["stable_harmonics"]) or "none",))
        lines.append(
            "recommended_harmonic_count: %d" % (
                item["recommended_harmonic_count"],))
        lines.append("fit_points: %d" % (item["fit_points"],))
        lines.append("prototype_only: 1")
        for direction in ("forward", "backward"):
            direction_fit = item["directions"][direction]
            waveform = direction_fit["waveform"]
            lines.append("%s:" % (direction,))
            for harmonic in direction_fit["harmonics"]:
                lines.append(
                    "  H%d: magnitude_mean=%.6f magnitude_stdev=%.6f "
                    "phase_deg_mean=%.3f" % (
                        harmonic["harmonic"],
                        harmonic["magnitude_mean"],
                        harmonic["magnitude_stdev"],
                        harmonic["phase_deg_mean"]))
            lines.append(
                "  waveform: peak_abs_raw=%.6f mean_raw=%.6f rms_raw=%.6f" % (
                    waveform["peak_abs_raw"],
                    waveform["mean_raw"],
                    waveform["rms_raw"]))
            preview = ", ".join(
                "%.6f" % (value,)
                for value in waveform["normalized"][:16])
            lines.append("  lut_preview_normalized[0:16]: %s" % (preview,))
        lines.append("note: %s" % (item["notes"],))
    return "\n".join(lines).rstrip()


def build_runtime_payload(fit_items):
    payloads = []
    for item in fit_items:
        directions = {}
        for direction, direction_fit in item["directions"].items():
            waveform = direction_fit["waveform"]
            direct_profile = build_direct_coil_profile(
                waveform["normalized"], item["fit_points"],
                DEFAULT_DIRECT_SHIFT_SCALE_DEG)
            directions[direction] = {
                "phase_points": item["fit_points"],
                "phase_units_per_cycle": item["fit_points"],
                "correction_domain": "accelerometer_normalized_phase_prototype",
                "stable_harmonics": item["stable_harmonics"],
                "harmonic_count": len(direction_fit["harmonics"]),
                "harmonics": direction_fit["harmonics"],
                "phase_offset_norm": waveform["normalized"],
                "phase_offset_q15": quantize_q15(waveform["normalized"]),
                "prototype_direct_profile": direct_profile,
            }
        payloads.append({
            "speed_mm_s": item["speed_mm_s"],
            "selected_axis": item["selected_axis"],
            "prototype_only": item["prototype_only"],
            "payload_kind": "u1_motor_phase_runtime_prototype",
            "notes": item["notes"],
            "directions": directions,
        })
    return payloads


def format_runtime_payload_report(payloads):
    lines = []
    for item in payloads:
        lines.append("runtime_payload_speed_mm_s: %.3f" % (item["speed_mm_s"],))
        lines.append("selected_axis: %s" % (item["selected_axis"],))
        lines.append("payload_kind: %s" % (item["payload_kind"],))
        lines.append("prototype_only: 1")
        for direction, direction_payload in item["directions"].items():
            lines.append("%s:" % (direction,))
            lines.append(
                "  phase_points=%d harmonic_count=%d stable_harmonics=%s" % (
                    direction_payload["phase_points"],
                    direction_payload["harmonic_count"],
                    ",".join(
                        str(h) for h in direction_payload["stable_harmonics"])
                    or "none"))
            preview_norm = ", ".join(
                "%.6f" % (value,)
                for value in direction_payload["phase_offset_norm"][:16])
            preview_q15 = ", ".join(
                str(value) for value in direction_payload["phase_offset_q15"][:16])
            lines.append("  phase_offset_norm[0:16]: %s" % (preview_norm,))
            lines.append("  phase_offset_q15[0:16]: %s" % (preview_q15,))
            direct_profile = direction_payload.get("prototype_direct_profile")
            if direct_profile is not None:
                preview_coil_a = ", ".join(
                    str(value)
                    for value in direct_profile["coil_a_unit_q15"][:8])
                preview_coil_b = ", ".join(
                    str(value)
                    for value in direct_profile["coil_b_unit_q15"][:8])
                lines.append(
                    "  direct_profile: kind=%s shift_scale_deg_default=%.3f" % (
                        direct_profile["profile_kind"],
                        direct_profile["shift_scale_deg_default"]))
                lines.append(
                    "  direct_coil_a_unit_q15[0:8]: %s" % (preview_coil_a,))
                lines.append(
                    "  direct_coil_b_unit_q15[0:8]: %s" % (preview_coil_b,))
        lines.append("note: %s" % (item["notes"],))
    return "\n".join(lines).rstrip()


def format_paired_capture_report(result):
    lines = [
        "paired_accel_file: %s" % (result["accel_file"],),
        "paired_trace_file: %s" % (result["trace_file"],),
        "selected_axis: %s" % (result["selected_axis"],),
        "overlap_window_s: %.6f..%.6f" % (
            result["overlap_start_s"], result["overlap_end_s"]),
        "overlap_duration_s: %.6f" % (result["overlap_duration_s"],),
        "interpolated_samples: %d" % (result["interpolated_sample_count"],),
        "trace_samples: %d" % (result["trace_sample_count"],),
        "trace_step_rate_hz: %.3f" % (result["trace_step_rate_hz"],),
        "nearest_trace_offset_s: mean=%.9f median/max approx via summary "
        "min=%.9f max=%.9f count=%d" % (
            result["nearest_trace_offset_s"]["mean"],
            result["nearest_trace_offset_s"]["min"],
            result["nearest_trace_offset_s"]["max"],
            result["nearest_trace_offset_s"]["count"]),
        "phase_bin_value_domain: %s" % (
            result["phase_bin_value_domain"],),
        "selected_axis_mean: %.6f" % (result["selected_axis_mean"],),
        "selected_axis_rms: %.6f" % (result["selected_axis_rms"],),
        "phase_bins: %d" % (result["phase_bins"],),
    ]
    for phase_bin in result["phase_bin_summary"]:
        lines.append(
            "  bin=%d count=%d mean=%.6f rms=%.6f min=%.6f max=%.6f" % (
                phase_bin["bin"], phase_bin["count"], phase_bin["mean"],
                phase_bin["rms"], phase_bin["min"], phase_bin["max"]))
    if result["time_domain_harmonics"]:
        harmonic = result["time_domain_harmonics"][0]
        lines.append(
            "time_domain_H1: freq=%.3fHz magnitude=%.6f phase_deg=%.3f" % (
                harmonic["frequency_hz"], harmonic["magnitude"],
                harmonic["phase_deg"]))
    return "\n".join(lines).rstrip()


def format_phase_residual_report(result):
    preview_curve = ", ".join(
        "%.6f" % (value,)
        for value in result["interpolated_residual"]["residual_curve"][:16])
    preview_q15 = ", ".join(
        str(value)
        for value in result["interpolated_residual"]["residual_q15"][:16])
    lines = [
        "phase_residual_selected_axis: %s" % (result["selected_axis"],),
        "phase_residual_points: %d" % (result["phase_residual_points"],),
        "phase_residual_peak_abs: %.6f" % (
            result["interpolated_residual"]["peak_abs"],),
        "phase_residual_mean_removed: %.6f" % (
            result["interpolated_residual"]["mean_removed"],),
        "phase_residual_bin_count: min=%d mean=%.3f max=%d" % (
            result["quality"]["min_bin_count"],
            result["quality"]["mean_bin_count"],
            result["quality"]["max_bin_count"]),
        "phase_residual_quality: score=%.3f spc=%.3f spc_score=%.3f "
        "phase_coverage=%.3f dominant_harmonic=%d harmonic_focus=%.3f "
        "harmonic_dominance=%s" % (
            result["quality"]["quality_score"],
            result["quality"]["samples_per_cycle"],
            result["quality"]["samples_per_cycle_score"],
            result["quality"]["populated_phase_fraction"],
            result["quality"]["dominant_harmonic"],
            result["quality"]["harmonic_focus"],
            (
                "%.3f" % (result["quality"]["harmonic_dominance_ratio"],)
                if math.isfinite(result["quality"]["harmonic_dominance_ratio"])
                else "inf")),
        "phase_residual_curve[0:16]: %s" % (preview_curve,),
        "phase_residual_q15[0:16]: %s" % (preview_q15,),
        "phase_residual_note: %s" % (result["notes"],),
    ]
    return "\n".join(lines).rstrip()


def format_phase_residual_aggregate_report(result):
    preview_curve = ", ".join(
        "%.6f" % (value,)
        for value in result["aggregated_curve"]["curve_mean"][:16])
    preview_curve_smoothed = ", ".join(
        "%.6f" % (value,)
        for value in result["aggregated_curve"]["curve_mean_smoothed"][:16])
    preview_stdev = ", ".join(
        "%.6f" % (value,)
        for value in result["aggregated_curve"]["curve_stdev"][:16])
    preview_q15 = ", ".join(
        str(value)
        for value in result["aggregated_curve"]["norm_q15"][:16])
    candidate_preview_q15 = ", ".join(
        str(value)
        for value in result["candidate_residual"]["norm_q15"][:16])
    candidate_harmonics = ",".join(
        "H%d" % (item["harmonic"],)
        for item in result["candidate_residual"]["harmonic_summary"]["harmonics"])
    recommended_harmonics = ",".join(
        "H%d" % (harmonic,)
        for harmonic in result["candidate_residual"]["harmonic_summary"][
            "recommended_harmonics"]) or "none"
    lines = [
        "aggregate_phase_residual_selected_axis: %s" % (
            result["selected_axis"],),
        "aggregate_phase_residual_points: %d" % (
            result["phase_residual_points"],),
        "aggregate_phase_residual_capture_count: %d" % (
            result["capture_count"],),
        "aggregate_phase_residual_acceptance: accepted=%d rejected=%d" % (
            result["accepted_capture_count"],
            result["rejected_capture_count"]),
        "aggregate_phase_residual_quality: mean_peak_abs=%.6f "
        "trace_step_rate_hz_mean=%.3f samples_per_cycle_mean=%.3f "
        "quality_score_mean=%.3f quality_score_min=%.3f "
        "quality_score_threshold=%.3f dominant_harmonic_mode=%d "
        "min_bin_count=%d mean_bin_count=%.3f max_bin_count=%d "
        "alignment_score_mean=%.6f shift_span=%d..%d "
        "alignment_strategy=%s smoothing_window=%d" % (
            result["quality"]["mean_peak_abs"],
            result["quality"]["trace_step_rate_hz_mean"],
            result["quality"]["samples_per_cycle_mean"],
            result["quality"]["quality_score_mean"],
            result["quality"]["quality_score_min"],
            result["quality"]["quality_score_threshold"],
            result["quality"]["dominant_harmonic_mode"],
            result["quality"]["min_bin_count"],
            result["quality"]["mean_bin_count"],
            result["quality"]["max_bin_count"],
            result["quality"]["alignment_score_mean"],
            result["quality"]["alignment_shift_span"]["min"],
            result["quality"]["alignment_shift_span"]["max"],
            result["quality"]["alignment_strategy"],
            result["quality"]["smoothing_window"]),
        "aggregate_phase_residual_curve_mean[0:16]: %s" % (preview_curve,),
        "aggregate_phase_residual_curve_mean_smoothed[0:16]: %s" % (
            preview_curve_smoothed,),
        "aggregate_phase_residual_curve_stdev[0:16]: %s" % (preview_stdev,),
        "aggregate_phase_residual_norm_q15[0:16]: %s" % (preview_q15,),
        "candidate_phase_residual: accepted=%d strategy=%s harmonic_lock=%s "
        "dominant_harmonic_mode=%d harmonics=%s recommended=%s "
        "q15[0:16]=%s" % (
            result["candidate_residual"]["accepted_capture_count"],
            result["candidate_residual"]["alignment_strategy"],
            (
                str(result["candidate_residual"]["alignment_harmonic_lock"])
                if result["candidate_residual"]["alignment_harmonic_lock"]
                is not None else "none"),
            result["candidate_residual"]["dominant_harmonic_mode"],
            candidate_harmonics,
            recommended_harmonics,
            candidate_preview_q15),
        "aggregate_phase_residual_note: %s" % (result["notes"],),
    ]
    if result["accepted_pairs"]:
        lines.append("accepted_pairs:")
        for item in result["accepted_pairs"]:
            details = item["alignment_details"]
            detail_text = ""
            if "harmonic" in details:
                detail_text = (
                    " harmonic=%d ref_phase=%.3f cand_phase=%.3f" % (
                        details["harmonic"],
                        details["reference"]["phase_deg"],
                        details["candidate"]["phase_deg"]))
            lines.append(
                "  %s shift=%d score=%.6f strategy=%s run_quality=%.3f "
                "spc=%.3f phase_coverage=%.3f min_bin_count=%d "
                "dominant_h=%d h_focus=%.3f h_dom=%s%s" % (
                    Path(item["accel_file"]).name,
                    item["shift"],
                    item["alignment_score"],
                    item["alignment_strategy"],
                    item["quality_score"],
                    item["samples_per_cycle"],
                    item["populated_phase_fraction"],
                    item["min_bin_count"],
                    item["dominant_harmonic"],
                    item["harmonic_focus"],
                    (
                        "%.3f" % (item["harmonic_dominance_ratio"],)
                        if math.isfinite(item["harmonic_dominance_ratio"])
                        else "inf"),
                    detail_text))
    if result["rejected_pairs"]:
        lines.append("rejected_pairs:")
        for item in result["rejected_pairs"]:
            lines.append(
                "  %s reasons=%s run_quality=%.3f spc=%.3f "
                "min_bin_count=%d dominant_h=%d" % (
                    Path(item["accel_file"]).name,
                    ",".join(item["reasons"]),
                    item["quality_score"],
                    item["samples_per_cycle"],
                    item["min_bin_count"],
                    item["dominant_harmonic"]))
    comparison = result.get("candidate_reference_comparison")
    if comparison is not None:
        q15_preview = ", ".join(str(value) for value in comparison["q15_delta_preview"])
        overlap = ",".join(
            "H%d" % (harmonic,)
            for harmonic in comparison["recommended_harmonics_overlap"]) or "none"
        lines.append("candidate_reference_comparison:")
        lines.append(
            "  reference=%s axis_match=%d candidate_axis=%s reference_axis=%s" % (
                Path(comparison["reference_file"]).name,
                1 if comparison["selected_axis_match"] else 0,
                comparison["selected_axis_candidate"],
                comparison["selected_axis_reference"]))
        lines.append(
            "  q15_delta: mean_abs=%.3f rms=%.3f max_abs=%d preview[0:16]=%s" % (
                comparison["q15_delta_summary"]["mean_abs"],
                comparison["q15_delta_summary"]["rms"],
                comparison["q15_delta_summary"]["max_abs"],
                q15_preview))
        lines.append(
            "  recommended_overlap=%s candidate=%s reference=%s" % (
                overlap,
                ",".join("H%d" % (h,) for h in comparison[
                    "recommended_harmonics_candidate"]) or "none",
                ",".join("H%d" % (h,) for h in comparison[
                    "recommended_harmonics_reference"]) or "none"))
        lines.append(
            "  refresh_recommended=%d reasons=%s thresholds(mean_abs_q15=%.3f,"
            " phase_delta_deg=%.3f)" % (
                1 if comparison["refresh_recommended"] else 0,
                ",".join(comparison["refresh_reasons"]) or "none",
                comparison["refresh_thresholds"]["mean_abs_q15_delta"],
                comparison["refresh_thresholds"]["phase_delta_deg"]))
        for item in comparison["harmonics"]:
            lines.append(
                "  H%d: share_delta=%.6f phase_delta_deg=%.3f "
                "candidate_recommended=%d reference_recommended=%d" % (
                    item["harmonic"],
                    item["magnitude_share_delta"],
                    item["phase_delta_deg"],
                    1 if item["candidate_recommended"] else 0,
                    1 if item["reference_recommended"] else 0))
    return "\n".join(lines).rstrip()


def format_runtime_trace_report(result):
    preview = []
    for row in result["preview"][:8]:
        preview.append(
            "idx=%d dt=%.6fs steps=%d phase=%d dmm=%.6f" % (
                row["flush_index"], row["window_duration_s"],
                row["generated_steps"], row["phase_delta"],
                row["generated_distance_mm"]))
    lines = [
        "runtime_trace_file: %s" % (Path(result["path"]).name,),
        "runtime_trace_flushes: total=%d active=%d zero_step=%d" % (
            result["flush_count"], result["active_flush_count"],
            result["zero_step_flush_count"]),
        "runtime_trace_duration_s: total=%.6f active=%.6f" % (
            result["total_duration_s"], result["active_duration_s"]),
        "runtime_trace_rates: flush=%.3fHz active_flush=%.3fHz" % (
            result["flush_rate_hz"], result["active_flush_rate_hz"]),
        "runtime_trace_window_s: mean=%.9f max=%.9f "
        "active_mean=%.9f active_max=%.9f" % (
            result["window_duration_s"]["mean"],
            result["window_duration_s"]["max"],
            result["active_window_duration_s"]["mean"],
            result["active_window_duration_s"]["max"]),
        "runtime_trace_generated_steps: mean_abs=%.3f max_abs=%d total_abs=%d" % (
            result["generated_steps"]["mean_abs"],
            result["generated_steps"]["max_abs"],
            result["generated_steps"]["total_abs"]),
        "runtime_trace_generated_distance_mm: mean_abs=%.6f max_abs=%.6f "
        "total_abs=%.6f" % (
            result["generated_distance_mm"]["mean_abs"],
            result["generated_distance_mm"]["max_abs"],
            result["generated_distance_mm"]["total_abs"]),
        "runtime_trace_phase_delta: mean_abs=%.3f max_abs=%d" % (
            result["phase_delta"]["mean_abs"],
            result["phase_delta"]["max_abs"]),
        "runtime_trace_preview: %s" % (" | ".join(preview),),
    ]
    return "\n".join(lines).rstrip()


def format_mcu_stats_report(result):
    lines = [
        "mcu_stats_file: %s" % (Path(result["path"]).name,),
        "mcu_stats_stepper: %s" % (result["stepper_name"],),
        "mcu_stats_chunks: queue_msgs=%d load_next=%d queued_moves=%d max_chunk=%d" % (
            result["queue_msgs"], result["load_next"],
            result["queued_moves"], result["max_chunk"]),
        "mcu_stats_execution: total_steps=%d timer_events=%d "
        "steps_per_load=%.3f steps_per_queue_msg=%.3f "
        "timer_events_per_step=%.3f" % (
            result["total_steps"], result["timer_events"],
            result["steps_per_load"], result["steps_per_queue_msg"],
            result["timer_events_per_step"]),
    ]
    return "\n".join(lines).rstrip()


def format_exec_trace_report(result):
    preview = []
    for row in result["preview"][:8]:
        preview.append(
            "idx=%d step=%d t=%.6f dt=%.6fs dsteps=%d" % (
                row["sample_index"], row["step_number"], row["step_time"],
                row["delta_step_time_s"], row["delta_steps"]))
    lines = [
        "exec_trace_file: %s" % (Path(result["path"]).name,),
        "exec_trace_samples: count=%d stride=%d total_steps=%d" % (
            result["sample_count"], result["trace_stride"],
            result["total_steps"]),
        "exec_trace_span: duration=%.6fs steps=%d sample_rate=%.3fHz "
        "observed_step_rate=%.3fHz" % (
            result["span_s"], result["span_steps"],
            result["sample_rate_hz"], result["observed_step_rate_hz"]),
        "exec_trace_delta_steps: mean=%.3f min=%s max=%s stdev=%.3f" % (
            result["delta_steps"]["mean"], result["delta_steps"]["min"],
            result["delta_steps"]["max"], result["delta_steps"]["stdev"]),
        "exec_trace_delta_time_s: mean=%.9f min=%.9f max=%.9f stdev=%.9f" % (
            result["delta_step_time_s"]["mean"],
            result["delta_step_time_s"]["min"],
            result["delta_step_time_s"]["max"],
            result["delta_step_time_s"]["stdev"]),
        "exec_trace_delta_clock: mean=%.3f min=%s max=%s stdev=%.3f "
        "min_interval_clock=%d max_interval_clock=%d" % (
            result["delta_step_clock"]["mean"],
            result["delta_step_clock"]["min"],
            result["delta_step_clock"]["max"],
            result["delta_step_clock"]["stdev"],
            result["min_interval_clock"],
            result["max_interval_clock"]),
        "exec_trace_preview: %s" % (" | ".join(preview),),
    ]
    return "\n".join(lines).rstrip()


def format_correction_plan_report(result):
    preview = []
    for row in result["preview"][:8]:
        preview.append(
            "idx=%d phase=%d rep=%s da=%d db=%d shift=%.6f" % (
                row["sample_index"], row["phase_index"],
                row["profile_representation"],
                row["delta_coil_a"], row["delta_coil_b"],
                row["profile_shift_norm"]))
    lines = [
        "correction_plan_file: %s" % (Path(result["path"]).name,),
        "correction_plan_samples: count=%d phase_min=%d phase_max=%d reps=%s" % (
            result["sample_count"], result["phase_index_min"],
            result["phase_index_max"],
            ",".join(result["representations"])),
        "correction_plan_delta: mean_abs=%.3f max_abs=%d "
        "delta_coil_a(mean=%.3f min=%s max=%s stdev=%.3f) "
        "delta_coil_b(mean=%.3f min=%s max=%s stdev=%.3f)" % (
            result["mean_abs_delta_coil"],
            result["max_abs_delta_coil"],
            result["delta_coil_a"]["mean"],
            result["delta_coil_a"]["min"],
            result["delta_coil_a"]["max"],
            result["delta_coil_a"]["stdev"],
            result["delta_coil_b"]["mean"],
            result["delta_coil_b"]["min"],
            result["delta_coil_b"]["max"],
            result["delta_coil_b"]["stdev"]),
        "correction_plan_shift_norm: mean_abs=%.6f max_abs=%.6f" % (
            result["mean_abs_shift_norm"],
            result["max_abs_shift_norm"]),
        "correction_plan_preview: %s" % (" | ".join(preview),),
    ]
    return "\n".join(lines).rstrip()


def format_correction_plan_residual_report(result):
    residual_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["residual_norm_0_16"])
    plan_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["aligned_plan_norm_0_16"])
    delta_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["delta_0_16"])
    harmonic_residual_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["harmonic_compare"]["preview"]["residual_norm_0_16"])
    harmonic_plan_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["harmonic_compare"]["preview"][
            "aligned_plan_norm_0_16"])
    harmonic_delta_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["harmonic_compare"]["preview"]["delta_0_16"])
    lines = [
        "correction_plan_residual_compare: file=%s axis=%s phase_points=%d "
        "plan_phase_count=%d phase_range=%d..%d reps=%s" % (
            Path(result["correction_plan_file"]).name,
            result["selected_axis"],
            result["phase_points"],
            result["plan_populated_phase_count"],
            result["plan_phase_index_min"],
            result["plan_phase_index_max"],
            ",".join(result["plan_representations"])),
        "correction_plan_residual_alignment: normal(score=%.6f shift=%d) "
        "inverted(score=%.6f shift=%d) selected=%s(score=%.6f shift=%d)" % (
            result["normal_alignment"]["score"],
            result["normal_alignment"]["shift"],
            result["inverted_alignment"]["score"],
            result["inverted_alignment"]["shift"],
            result["selected_alignment"]["polarity"],
            result["selected_alignment"]["score"],
            result["selected_alignment"]["shift"]),
        "correction_plan_residual_delta: mean_abs=%.6f rms=%.6f max_abs=%.6f" % (
            result["delta_summary"]["mean_abs"],
            result["delta_summary"]["rms"],
            result["delta_summary"]["max_abs"]),
        "correction_plan_residual_harmonic_compare: harmonics=%s source=%s "
        "normal(score=%.6f shift=%d) inverted(score=%.6f shift=%d) "
        "selected=%s(score=%.6f shift=%d) mean_abs=%.6f rms=%.6f "
        "max_abs=%.6f" % (
            ",".join("H%d" % harmonic
                     for harmonic in result["harmonic_compare"]["harmonics"]),
            result["harmonic_compare"]["harmonic_source"],
            result["harmonic_compare"]["normal_alignment"]["score"],
            result["harmonic_compare"]["normal_alignment"]["shift"],
            result["harmonic_compare"]["inverted_alignment"]["score"],
            result["harmonic_compare"]["inverted_alignment"]["shift"],
            result["harmonic_compare"]["selected_alignment"]["polarity"],
            result["harmonic_compare"]["selected_alignment"]["score"],
            result["harmonic_compare"]["selected_alignment"]["shift"],
            result["harmonic_compare"]["delta_summary"]["mean_abs"],
            result["harmonic_compare"]["delta_summary"]["rms"],
            result["harmonic_compare"]["delta_summary"]["max_abs"]),
        "correction_plan_residual_preview_residual[0:16]: %s" % (
            residual_preview,),
        "correction_plan_residual_preview_plan[0:16]: %s" % (
            plan_preview,),
        "correction_plan_residual_preview_delta[0:16]: %s" % (
            delta_preview,),
        "correction_plan_residual_harmonic_preview_residual[0:16]: %s" % (
            harmonic_residual_preview,),
        "correction_plan_residual_harmonic_preview_plan[0:16]: %s" % (
            harmonic_plan_preview,),
        "correction_plan_residual_harmonic_preview_delta[0:16]: %s" % (
            harmonic_delta_preview,),
        "correction_plan_residual_note: %s" % (result["notes"],),
    ]
    return "\n".join(lines).rstrip()


def format_correction_plan_residual_aggregate_report(result):
    residual_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["mean_residual_curve_0_16"])
    plan_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["mean_aligned_plan_curve_0_16"])
    delta_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["mean_delta_curve_0_16"])
    delta_stdev_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["stdev_delta_curve_0_16"])
    harmonic_residual_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["harmonic_mean_residual_curve_0_16"])
    harmonic_plan_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["harmonic_mean_aligned_plan_curve_0_16"])
    harmonic_delta_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["harmonic_mean_delta_curve_0_16"])
    harmonic_delta_stdev_preview = ", ".join(
        "%.6f" % (value,)
        for value in result["preview"]["harmonic_stdev_delta_curve_0_16"])
    lines = [
        "aggregate_correction_plan_residual: count=%d axis=%s polarity=%s "
        "phase_points=%d plan_phase_count=%d reps=%s" % (
            result["capture_count"],
            result["selected_axis_mode"],
            result["selected_polarity_mode"],
            result["phase_points"],
            result["plan_populated_phase_count_mode"],
            ",".join(result["plan_representations"])),
        "aggregate_correction_plan_residual_scores: "
        "selected(mean=%.6f min=%.6f max=%.6f stdev=%.6f) "
        "shift(mean=%.3f min=%s max=%s stdev=%.3f)" % (
            result["selected_score"]["mean"],
            result["selected_score"]["min"],
            result["selected_score"]["max"],
            result["selected_score"]["stdev"],
            result["selected_shift"]["mean"],
            result["selected_shift"]["min"],
            result["selected_shift"]["max"],
            result["selected_shift"]["stdev"]),
        "aggregate_correction_plan_residual_harmonic_scores: "
        "harmonics=%s source=%s polarity=%s "
        "selected(mean=%.6f min=%.6f max=%.6f stdev=%.6f) "
        "shift(mean=%.3f min=%s max=%s stdev=%.3f)" % (
            ",".join("H%d" % harmonic for harmonic in result["harmonic_set_mode"]),
            result["harmonic_source_mode"],
            result["harmonic_selected_polarity_mode"],
            result["harmonic_selected_score"]["mean"],
            result["harmonic_selected_score"]["min"],
            result["harmonic_selected_score"]["max"],
            result["harmonic_selected_score"]["stdev"],
            result["harmonic_selected_shift"]["mean"],
            result["harmonic_selected_shift"]["min"],
            result["harmonic_selected_shift"]["max"],
            result["harmonic_selected_shift"]["stdev"]),
        "aggregate_correction_plan_residual_delta: "
        "mean_abs(mean=%.6f min=%.6f max=%.6f stdev=%.6f) "
        "rms(mean=%.6f min=%.6f max=%.6f stdev=%.6f) "
        "max_abs(mean=%.6f min=%.6f max=%.6f stdev=%.6f)" % (
            result["mean_abs_delta"]["mean"],
            result["mean_abs_delta"]["min"],
            result["mean_abs_delta"]["max"],
            result["mean_abs_delta"]["stdev"],
            result["rms_delta"]["mean"],
            result["rms_delta"]["min"],
            result["rms_delta"]["max"],
            result["rms_delta"]["stdev"],
            result["max_abs_delta"]["mean"],
            result["max_abs_delta"]["min"],
            result["max_abs_delta"]["max"],
            result["max_abs_delta"]["stdev"]),
        "aggregate_correction_plan_residual_harmonic_delta: "
        "mean_abs(mean=%.6f min=%.6f max=%.6f stdev=%.6f) "
        "rms(mean=%.6f min=%.6f max=%.6f stdev=%.6f) "
        "max_abs(mean=%.6f min=%.6f max=%.6f stdev=%.6f)" % (
            result["harmonic_mean_abs_delta"]["mean"],
            result["harmonic_mean_abs_delta"]["min"],
            result["harmonic_mean_abs_delta"]["max"],
            result["harmonic_mean_abs_delta"]["stdev"],
            result["harmonic_rms_delta"]["mean"],
            result["harmonic_rms_delta"]["min"],
            result["harmonic_rms_delta"]["max"],
            result["harmonic_rms_delta"]["stdev"],
            result["harmonic_max_abs_delta"]["mean"],
            result["harmonic_max_abs_delta"]["min"],
            result["harmonic_max_abs_delta"]["max"],
            result["harmonic_max_abs_delta"]["stdev"]),
        "aggregate_correction_plan_residual_preview_residual[0:16]: %s" % (
            residual_preview,),
        "aggregate_correction_plan_residual_preview_plan[0:16]: %s" % (
            plan_preview,),
        "aggregate_correction_plan_residual_preview_delta[0:16]: %s" % (
            delta_preview,),
        "aggregate_correction_plan_residual_preview_delta_stdev[0:16]: %s" % (
            delta_stdev_preview,),
        "aggregate_correction_plan_residual_harmonic_preview_residual[0:16]: %s" % (
            harmonic_residual_preview,),
        "aggregate_correction_plan_residual_harmonic_preview_plan[0:16]: %s" % (
            harmonic_plan_preview,),
        "aggregate_correction_plan_residual_harmonic_preview_delta[0:16]: %s" % (
            harmonic_delta_preview,),
        "aggregate_correction_plan_residual_harmonic_preview_delta_stdev[0:16]: %s" % (
            harmonic_delta_stdev_preview,),
    ]
    for item in result["source_files"]:
        lines.append(
            "  file=%s polarity=%s score=%.6f mean_abs_delta=%.6f rms_delta=%.6f "
            "harmonic_polarity=%s harmonic_score=%.6f "
            "harmonic_mean_abs_delta=%.6f harmonic_rms_delta=%.6f"
            % (
                Path(item["correction_plan_file"]).name,
                item["polarity"],
                item["score"],
                item["mean_abs_delta"],
                item["rms_delta"],
                item["harmonic_polarity"],
                item["harmonic_score"],
                item["harmonic_mean_abs_delta"],
                item["harmonic_rms_delta"]))
    lines.append(
        "aggregate_correction_plan_residual_note: %s" % (result["notes"],))
    return "\n".join(lines).rstrip()


def main():
    opts = parse_args()
    if (opts.candidate_residual_out or opts.candidate_reference) and not (
            opts.aggregate_phase_residual and opts.export_phase_residual):
        raise SystemExit(
            "--candidate-residual-out/--candidate-reference require "
            "--aggregate-phase-residual and --export-phase-residual")
    if opts.aggregate_correction_plan_residual and not (
            opts.aggregate_phase_residual and opts.export_phase_residual):
        raise SystemExit(
            "--aggregate-correction-plan-residual requires "
            "--aggregate-phase-residual and --export-phase-residual")
    results = [
        analyze_file(Path(csv_file), opts)
        for csv_file in opts.csv_files
    ]
    paired_results = []
    if opts.trace_file is not None and opts.aggregate_phase_residual:
        raise SystemExit(
            "--trace-file and --aggregate-phase-residual are mutually exclusive")
    if opts.trace_file is not None:
        if len(opts.csv_files) != 1:
            raise SystemExit("--trace-file currently requires exactly one accel CSV")
        paired_results = [analyze_paired_capture(
            Path(opts.csv_files[0]), Path(opts.trace_file), opts)]
    elif opts.aggregate_phase_residual:
        paired_results = [
            analyze_paired_capture(Path(csv_file), auto_resolve_trace_path(Path(csv_file)), opts)
            for csv_file in opts.csv_files
        ]
    paired_result = paired_results[0] if len(paired_results) == 1 else None
    phase_residuals = []
    if opts.export_phase_residual:
        if not paired_results:
            raise SystemExit(
                "--export-phase-residual requires --trace-file or "
                "--aggregate-phase-residual")
        phase_residuals = [
            build_phase_residual_export(item, opts)
            for item in paired_results
        ]
    phase_residual = phase_residuals[0] if len(phase_residuals) == 1 else None
    aggregate_phase_residual = None
    if opts.aggregate_phase_residual and opts.export_phase_residual:
        aggregate_phase_residual = build_phase_residual_aggregate(
            phase_residuals, opts)
        if opts.candidate_reference:
            reference_path = Path(opts.candidate_reference)
            reference_candidate = load_candidate_residual(reference_path)
            comparison = compare_candidate_residuals(
                aggregate_phase_residual["candidate_residual"],
                reference_candidate, reference_path, opts)
            aggregate_phase_residual[
                "candidate_reference_comparison"] = comparison
        if opts.candidate_residual_out:
            candidate_path = Path(opts.candidate_residual_out)
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            with open(candidate_path, "w") as handle:
                json.dump(
                    aggregate_phase_residual["candidate_residual"],
                    handle, indent=2)
    comparisons = compare_forward_backward(results, opts) if opts.compare_fb else []
    runtime_trace = None
    if opts.runtime_trace_file is not None:
        runtime_trace = analyze_runtime_trace(Path(opts.runtime_trace_file))
    mcu_stats = None
    if opts.mcu_stats_file is not None:
        mcu_stats = analyze_mcu_stats(Path(opts.mcu_stats_file))
    exec_trace = None
    if opts.exec_trace_file is not None:
        exec_trace = analyze_exec_trace(Path(opts.exec_trace_file))
    correction_plan = None
    if opts.correction_plan_file is not None:
        correction_plan = analyze_correction_plan(
            Path(opts.correction_plan_file))
    correction_plan_residual = None
    if correction_plan is not None and phase_residual is not None:
        correction_plan_residual = compare_correction_plan_to_phase_residual(
            Path(opts.correction_plan_file), phase_residual, opts)
    aggregate_correction_plan_residual = None
    if opts.aggregate_correction_plan_residual:
        if not phase_residuals:
            raise SystemExit(
                "--aggregate-correction-plan-residual requires paired phase "
                "residual exports")
        correction_plan_residuals = [
            compare_correction_plan_to_phase_residual(
                auto_resolve_correction_plan_path(Path(csv_file)),
                phase_residuals[index], opts)
            for index, csv_file in enumerate(opts.csv_files)
        ]
        aggregate_correction_plan_residual = (
            build_correction_plan_residual_aggregate(
                correction_plan_residuals))
    aggregates = (
        aggregate_forward_backward(results, opts)
        if opts.aggregate_fb else [])
    basis_items = build_harmonic_basis(aggregates, opts) if opts.export_basis else []
    fit_items = build_fit_export(basis_items, opts) if opts.export_fit else []
    runtime_payloads = (
        build_runtime_payload(fit_items) if opts.export_runtime_payload else [])
    if opts.json:
        payload = {"results": results}
        if paired_result is not None:
            payload["paired_capture"] = paired_result
        elif paired_results:
            payload["paired_captures"] = paired_results
        if phase_residual is not None:
            payload["phase_residual"] = phase_residual
        elif phase_residuals:
            payload["phase_residuals"] = phase_residuals
        if aggregate_phase_residual is not None:
            payload["aggregate_phase_residual"] = aggregate_phase_residual
        if runtime_trace is not None:
            payload["runtime_trace"] = runtime_trace
        if mcu_stats is not None:
            payload["mcu_stats"] = mcu_stats
        if exec_trace is not None:
            payload["exec_trace"] = exec_trace
        if correction_plan is not None:
            payload["correction_plan"] = correction_plan
        if correction_plan_residual is not None:
            payload["correction_plan_residual"] = correction_plan_residual
        if aggregate_correction_plan_residual is not None:
            payload["aggregate_correction_plan_residual"] = (
                aggregate_correction_plan_residual)
        if opts.compare_fb:
            payload["compare_fb"] = comparisons
        if opts.aggregate_fb:
            payload["aggregate_fb"] = aggregates
        if opts.export_basis:
            payload["harmonic_basis"] = basis_items
        if opts.export_fit:
            payload["harmonic_fit"] = fit_items
        if opts.export_runtime_payload:
            payload["runtime_payload"] = runtime_payloads
        print(json.dumps(payload, indent=2))
        return
    for index, result in enumerate(results):
        if index:
            print()
        print(format_text_report(result))
    if paired_result is not None:
        if results:
            print()
        print(format_paired_capture_report(paired_result))
    elif paired_results:
        if results:
            print()
        for index, item in enumerate(paired_results):
            if index:
                print()
            print(format_paired_capture_report(item))
    if phase_residual is not None:
        print()
        print(format_phase_residual_report(phase_residual))
    elif phase_residuals:
        for item in phase_residuals:
            print()
            print(format_phase_residual_report(item))
    if aggregate_phase_residual is not None:
        print()
        print(format_phase_residual_aggregate_report(aggregate_phase_residual))
    if runtime_trace is not None:
        print()
        print(format_runtime_trace_report(runtime_trace))
    if mcu_stats is not None:
        print()
        print(format_mcu_stats_report(mcu_stats))
    if exec_trace is not None:
        print()
        print(format_exec_trace_report(exec_trace))
    if correction_plan is not None:
        print()
        print(format_correction_plan_report(correction_plan))
    if correction_plan_residual is not None:
        print()
        print(format_correction_plan_residual_report(
            correction_plan_residual))
    if aggregate_correction_plan_residual is not None:
        print()
        print(format_correction_plan_residual_aggregate_report(
            aggregate_correction_plan_residual))
    if opts.compare_fb:
        if results:
            print()
        if comparisons:
            print(format_compare_report(comparisons))
        else:
            print("warning: no forward/backward file pairs found for comparison")
    if opts.aggregate_fb:
        if results or comparisons:
            print()
        if aggregates:
            print(format_aggregate_report(aggregates))
        else:
            print("warning: no forward/backward file groups found for aggregation")
    if opts.export_basis:
        if results or comparisons or aggregates:
            print()
        if basis_items:
            print(format_basis_report(basis_items))
        else:
            print("warning: no aggregated forward/backward groups found for basis export")
    if opts.export_fit:
        if results or comparisons or aggregates or basis_items:
            print()
        if fit_items:
            print(format_fit_report(fit_items))
        else:
            print("warning: no basis items found for fit export")
    if opts.export_runtime_payload:
        if results or comparisons or aggregates or basis_items or fit_items:
            print()
        if runtime_payloads:
            print(format_runtime_payload_report(runtime_payloads))
        else:
            print("warning: no fit items found for runtime payload export")


if __name__ == "__main__":
    main()
