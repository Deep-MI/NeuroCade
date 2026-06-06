# Rhineland Study MRI protocol 

This data repository contains an example of the acquired MRI sequences and the output of the post-processing pipelines in the Rhineland Study. An overview and information about the MRI sequences and post-processing pipelines can be found in our work 'Versatile MRI Acquisition and Processing Protocol for Population-Based Neuroimaging.' [PAPER](https://doi.org/10.1038/s41596-024-01085-w).

## Local Download Helper

To download the raw Rhineland MRI example into `RLS_case_all`, run:

```bash
./download_sub_rs_mri_proc.sh
```

By default, the script only downloads and extracts:

* `sub_rs_mri_raw.zip`

into:

* `./RLS_case_all/sub_rs_mri_raw`

If you want the full Rhineland example bundle as well, use:

```bash
./download_sub_rs_mri_proc.sh --full
```

That also downloads and extracts:

* `sub_rs_mri_proc.zip`
* `sub_rs_mri_struc_only.zip`

* `./RLS_case_all/sub_rs_mri_proc`
* `./RLS_case_all/sub_rs_mri_struc_only`

## Build the App Sample Case

The app's seeded sample case is no longer assembled from the downloaded processed archives. Instead, it is generated from a real FastSurfer run on the Rhineland T1-weighted sample scan:

* input: `./RLS_case_all/sub_rs_mri_raw/T1_RMS.nii.gz`
* full output: `./generated_fastsurfer/Rhineland_0000`
* curated app seed: `./FastSurfer_Rhineland_0000`

From the repo root, the streamlined one-shot call is:

```bash
./scripts/process_demo_case.sh
```

That helper downloads the raw Rhineland sample automatically when needed and then invokes this directory's builder script.

Build it with:

```bash
./create_fastsurfer_sample_case.sh
```

If `./generated_fastsurfer/Rhineland_0000` already exists, the script now reuses that full FastSurfer output by default and simply rebuilds the curated `./FastSurfer_Rhineland_0000` seed directory.

Optional overrides:

```bash
THREADS=8 DEVICE_MODE=cuda ./create_fastsurfer_sample_case.sh
```

Or from the repo root:

```bash
./scripts/process_demo_case.sh --threads 8 --device cuda
```

`DEVICE_MODE=auto` prefers CUDA, but it now falls back to CPU automatically if the selected FastSurfer image does not support the host GPU architecture.

Prerequisite:

- the full FastSurfer pipeline requires a FreeSurfer license
- the builder now standardizes on `../neurocade-data/license.txt`, which is the same `/data/license.txt` source used by the runtime tools
- if that file is missing, it will populate it from `FREESURFER_LICENSE`

The seeded app sample case now uses only `./FastSurfer_Rhineland_0000`.
If that generated directory is missing, the app will not seed a sample case until you run `./create_fastsurfer_sample_case.sh`.


**MRI sequences**

An example of all available sequences in NIfTI format from the Rhineland Study MRI protocol is provided in the *sub_rs_mri_raw* folder. You can find them as follows:


```  bash
| -- sub_rs_mri_raw
    (Scout image)
    |-- Scout.nii.gz 
    (B1 mapping)
    |-- DREAM_B1.nii.gz 
    |-- DREAM_B1_Phase.nii.gz
    |-- B1Map.nii.gz
    |-- RefVolt.nii.gz
    (B0 mapping)
    |-- B0.nii.gz
    |-- B0_Phase.nii.gz 
    (Resting state functional MRI)
    |-- RestingState_E00_M.nii.gz
    (T1-weighted)
    |-- T1_RMS.nii.gz
    (T2-weighted)
    |-- T2_caipi.nii.gz
    (FLAIR)
    |-- FLAIR.nii.gz
    (Diffusion-weighted / CS-DSI)
    |-- DiffusionDSI.nii.gz
    |-- DiffusionDSI_r.nii.gz
    (T2*/susceptibility-weighted)
    |-- QSMEPI_AP.nii.gz
    |-- QSMEPI_AP_Phase.nii.gz
    |-- QSMEPI_PA.nii.gz
    |-- QSMEPI_PA_Phase.nii.gz
    (T2-weighted hippocampal subfields)
    |-- T2_HippocampalSubfields.nii.gz
    (Body scout)
    |-- BodyScout.nii.gz
    (Body Fat)
    |-- FatImaging_F.nii.gz
    |-- FatImaging_W.nii.gz
    |-- FatImaging_in.nii.gz
    |-- FatImaging_opp.nii.gz
```
The *sub_rs_mri_struc_only* folder is generated locally from the Zenodo raw archive and contains only T1, T2*, and FLAIR sequences for users interested exclusively in structural imaging data.

**Processing Pipelines**

The post-processing results presented in the *sub_rs_mri_proc* folder are obtained by processing the example scan (*sub_rs_mri_raw*) using the implementation available at [Rhineland Study](https://github.com/rhinelandstudy). The results provided are from the following pipelines:

* `adipose_pipeline` : In-house abdominal adipose segmentation pipeline
* `freesurfer6_pipeline`: Whole brain segmentation with FreeSurfer 6.0
* `ob_pipeline`: In-house olfactory bulb segmentation pipeline
* `rsfmri_pipeline`: In-house resting state MRI pipeline
* `wmhs_pipeline`: In-house white matter hyperintensities segmentation pipeline
* `dsi_pipeline`: In-house Diffusion pipeline
* `qsm_pipeline`: In-house QSM pipeline


Additional information of the outcomes for each pipeline can be found in their corresponding README file.
