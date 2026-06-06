#!/usr/bin/env bash
# Purpose:
#   Prepares sample case data for the NeuroCade demo workflow.


set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RAW_T1_REL="RLS_case_all/sub_rs_mri_raw/T1_RMS.nii.gz"
RAW_T1_PATH="$SCRIPT_DIR/$RAW_T1_REL"
FASTSURFER_OUTPUT_ROOT="$SCRIPT_DIR/generated_fastsurfer"
SUBJECT_ID="Rhineland_0000"
FASTSURFER_SUBJECT_DIR="$FASTSURFER_OUTPUT_ROOT/$SUBJECT_ID"
SAMPLE_CASE_DIR="$SCRIPT_DIR/FastSurfer_Rhineland_0000"
RUNTIME_LICENSE_PATH="$REPO_ROOT/neurocade-data/license.txt"
THREADS="${THREADS:-4}"
DEVICE_MODE="${DEVICE_MODE:-auto}"
APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"
FREESURFER_LICENSE_PATH="${FREESURFER_LICENSE:-}"
REUSE_GENERATED="${REUSE_GENERATED:-1}"

SELECTED_OUTPUTS=(
  "mri/orig.mgz:mri/orig.mgz"
  "mri/001.mgz|mri/orig/001.mgz:mri/001.mgz"
  "mri/orig_nu.mgz:mri/orig_nu.mgz"
  "mri/mask.mgz:mri/mask.mgz"
  "mri/aseg.auto_noCCseg.mgz:mri/aseg.auto_noCCseg.mgz"
  "mri/aparc.DKTatlas+aseg.deep.mgz:mri/aparc.DKTatlas+aseg.deep.mgz"
  "mri/aparc.DKTatlas+aseg.deep.withCC.mgz:mri/aparc.DKTatlas+aseg.deep.withCC.mgz"
  "mri/wmparc.DKTatlas.mapped.mgz:mri/wmparc.DKTatlas.mapped.mgz"
  "surf/lh.pial|surf/lh.pial.T1:surf/lh.pial"
  "surf/rh.pial|surf/rh.pial.T1:surf/rh.pial"
  "surf/lh.white:surf/lh.white"
  "surf/rh.white:surf/rh.white"
  "surf/lh.curv:surf/lh.curv"
  "surf/rh.curv:surf/rh.curv"
  "label/lh.aparc.DKTatlas.mapped.annot|label/lh.aparc.DKTatlas.annot:label/lh.aparc.DKTatlas.mapped.annot"
  "label/rh.aparc.DKTatlas.mapped.annot|label/rh.aparc.DKTatlas.annot:label/rh.aparc.DKTatlas.mapped.annot"
  "scripts/recon-surf.log:logs/stdout.log"
  "scripts/deep-seg.log:logs/stderr.log"
)

usage() {
  cat <<EOF
Usage: ./create_fastsurfer_sample_case.sh [--threads N] [--device auto|cpu|cuda] [--reuse-generated|--force-run]

Build the app's seeded sample case from a real FastSurfer run using:
  $RAW_T1_REL

Outputs:
  Full FastSurfer run: $FASTSURFER_OUTPUT_ROOT/$SUBJECT_ID
  Curated app sample:  $SAMPLE_CASE_DIR

If $FASTSURFER_SUBJECT_DIR already exists, the script reuses it by default and
only rebuilds the curated app sample. Set REUSE_GENERATED=0 or pass --force-run
to recompute the full FastSurfer output from scratch.
EOF
}

container_supports_host_cuda() {
  local host_capabilities supported_arches first_cap

  if ! command -v nvidia-smi >/dev/null 2>&1; then
    return 1
  fi

  host_capabilities="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null || true)"
  first_cap="$(printf '%s\n' "$host_capabilities" | sed -n '1s/[.[:space:]]//gp')"
  if [[ -z "$first_cap" ]]; then
    return 1
  fi

  supported_arches="$(
    "$APPTAINER_BIN" exec --cleanenv --no-home --nv \
      "$FASTSURFER_IMAGE" \
      python3 -c 'import torch; print(" ".join(a.replace("sm_", "") for a in torch.cuda.get_arch_list() if a.startswith("sm_")))' \
      2>/dev/null || true
  )"
  if [[ -z "$supported_arches" ]]; then
    return 1
  fi

  [[ " $supported_arches " == *" $first_cap "* ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --threads)
      THREADS="$2"
      shift 2
      ;;
    --device)
      DEVICE_MODE="$2"
      shift 2
      ;;
    --reuse-generated)
      REUSE_GENERATED="1"
      shift
      ;;
    --force-run)
      REUSE_GENERATED="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

fastsurfer_output_complete() {
  [[ -f "$FASTSURFER_SUBJECT_DIR/mri/orig.mgz" ]] && \
  [[ -f "$FASTSURFER_SUBJECT_DIR/mri/aparc.DKTatlas+aseg.deep.mgz" ]] && \
  [[ -f "$FASTSURFER_SUBJECT_DIR/scripts/deep-seg.log" ]]
}

mkdir -p "$FASTSURFER_OUTPUT_ROOT"

if [[ "$REUSE_GENERATED" == "1" && -d "$FASTSURFER_SUBJECT_DIR" ]] && ! fastsurfer_output_complete; then
  echo "Warning: existing FastSurfer output at $FASTSURFER_SUBJECT_DIR is incomplete; rebuilding from scratch." >&2
  REUSE_GENERATED="0"
fi

if [[ "$REUSE_GENERATED" != "1" || ! -d "$FASTSURFER_SUBJECT_DIR" ]]; then
  if [[ ! -f "$RAW_T1_PATH" ]]; then
    echo "Error: missing T1w input at $RAW_T1_PATH" >&2
    echo "Run ./download_sub_rs_mri_proc.sh first to download the raw Rhineland sample data." >&2
    exit 1
  fi

  if ! command -v "$APPTAINER_BIN" >/dev/null 2>&1; then
    echo "Error: Apptainer is required to build the FastSurfer sample case." >&2
    exit 1
  fi
  FASTSURFER_IMAGE="$("$REPO_ROOT/scripts/containers.sh" path fastsurfer 2>/dev/null || true)"
  if [[ -z "$FASTSURFER_IMAGE" || ! -f "$FASTSURFER_IMAGE" ]]; then
    echo "Error: FastSurfer container is not installed. Run: $REPO_ROOT/scripts/containers.sh install fastsurfer" >&2
    exit 1
  fi

  if [[ -z "$FREESURFER_LICENSE_PATH" && -f "$RUNTIME_LICENSE_PATH" ]]; then
    FREESURFER_LICENSE_PATH="$RUNTIME_LICENSE_PATH"
  fi

  if [[ -z "$FREESURFER_LICENSE_PATH" || ! -f "$FREESURFER_LICENSE_PATH" ]]; then
    echo "Error: a FreeSurfer license is required for the full FastSurfer pipeline." >&2
    echo "Provide one of:" >&2
    echo "  - neurocade-data/license.txt (same /data/license.txt used by runtime tools)" >&2
    echo "  - FREESURFER_LICENSE=/abs/path/to/license.txt" >&2
    exit 1
  fi

  if [[ "$FREESURFER_LICENSE_PATH" != "$RUNTIME_LICENSE_PATH" ]]; then
    mkdir -p "$(dirname "$RUNTIME_LICENSE_PATH")"
    cp "$FREESURFER_LICENSE_PATH" "$RUNTIME_LICENSE_PATH"
    chmod 600 "$RUNTIME_LICENSE_PATH" || true
    FREESURFER_LICENSE_PATH="$RUNTIME_LICENSE_PATH"
  fi

  case "$DEVICE_MODE" in
    auto)
      if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        if container_supports_host_cuda; then
          FASTSURFER_DEVICE="cuda"
          APPTAINER_GPU_ARGS=(--nv)
        else
          echo "Warning: $FASTSURFER_IMAGE does not support the host GPU architecture; falling back to CPU." >&2
          FASTSURFER_DEVICE="cpu"
          APPTAINER_GPU_ARGS=()
        fi
      else
        FASTSURFER_DEVICE="cpu"
        APPTAINER_GPU_ARGS=()
      fi
      ;;
    cuda|gpu)
      FASTSURFER_DEVICE="cuda"
      APPTAINER_GPU_ARGS=(--nv)
      ;;
    cpu)
      FASTSURFER_DEVICE="cpu"
      APPTAINER_GPU_ARGS=()
      ;;
    *)
      echo "Error: unsupported device mode '$DEVICE_MODE'. Use auto, cpu, or cuda." >&2
      exit 1
      ;;
  esac

  rm -rf "$FASTSURFER_SUBJECT_DIR"

  echo "Running FastSurfer sample build with image: $FASTSURFER_IMAGE"
  echo "  Input:   $RAW_T1_PATH"
  echo "  Output:  $FASTSURFER_SUBJECT_DIR"
  echo "  Device:  $FASTSURFER_DEVICE"
  echo "  Threads: $THREADS"
  echo "  License: $FREESURFER_LICENSE_PATH"

  apptainer_cmd=(
    "$APPTAINER_BIN" exec --cleanenv --no-home
  )
  if (( ${#APPTAINER_GPU_ARGS[@]} )); then
    apptainer_cmd+=("${APPTAINER_GPU_ARGS[@]}")
  fi
  apptainer_cmd+=(
    --bind "$SCRIPT_DIR:/sample_case:rw"
    --bind "$FREESURFER_LICENSE_PATH:/fs_license.txt:ro"
    "$FASTSURFER_IMAGE"
    /fastsurfer/run_fastsurfer.sh
    --t1 "/sample_case/$RAW_T1_REL"
    --sd /sample_case/generated_fastsurfer
    --sid "$SUBJECT_ID"
    --threads "$THREADS"
    --device "$FASTSURFER_DEVICE"
    --viewagg_device "$FASTSURFER_DEVICE"
    --fs_license /fs_license.txt
    --allow_root
  )
  "${apptainer_cmd[@]}"
else
  echo "Reusing existing FastSurfer output at $FASTSURFER_SUBJECT_DIR"
fi

if [[ ! -d "$FASTSURFER_SUBJECT_DIR" ]]; then
  echo "Error: expected FastSurfer output directory $FASTSURFER_SUBJECT_DIR was not created." >&2
  exit 1
fi

rm -rf "$SAMPLE_CASE_DIR"
tmp_dir="$(mktemp -d)"
for selection in "${SELECTED_OUTPUTS[@]}"; do
  source_spec="${selection%%:*}"
  target_name="${selection##*:}"
  source_path=""
  IFS='|' read -r -a source_candidates <<< "$source_spec"
  for candidate in "${source_candidates[@]}"; do
    if [[ -f "$FASTSURFER_SUBJECT_DIR/$candidate" ]]; then
      source_path="$FASTSURFER_SUBJECT_DIR/$candidate"
      break
    fi
  done
  if [[ -z "$source_path" ]]; then
    rm -rf "$tmp_dir"
    echo "Error: expected one of ${source_candidates[*]} under $FASTSURFER_SUBJECT_DIR was not found." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$tmp_dir/$target_name")"
  cp "$source_path" "$tmp_dir/$target_name"
done

mv "$tmp_dir" "$SAMPLE_CASE_DIR"

echo "Done."
echo "  Full FastSurfer output: $FASTSURFER_SUBJECT_DIR"
echo "  App sample case:        $SAMPLE_CASE_DIR"
