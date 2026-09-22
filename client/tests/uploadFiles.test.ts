import assert from 'node:assert/strict';
import test from 'node:test';

import {
  droppedUploadFiles,
  inferredUploadName,
  summarizeUploadSelection,
  uploadRelativePath,
} from '../src/utils/uploadFiles.js';

function fileEntry(path: string, contents: string): FileSystemFileEntry {
  return {
    isFile: true,
    isDirectory: false,
    name: path.split('/').at(-1) ?? path,
    fullPath: `/${path}`,
    filesystem: {} as FileSystem,
    getParent: () => undefined,
    file: (success: (file: File) => void) => success(new File([contents], path.split('/').at(-1) ?? path)),
  };
}

function directoryEntry(path: string, children: FileSystemEntry[]): FileSystemDirectoryEntry {
  return {
    isFile: false,
    isDirectory: true,
    name: path.split('/').at(-1) ?? path,
    fullPath: `/${path}`,
    filesystem: {} as FileSystem,
    getParent: () => undefined,
    getDirectory(
      _path?: string | null,
      _options?: FileSystemFlags,
      _successCallback?: FileSystemEntryCallback,
      _errorCallback?: ErrorCallback,
    ) {
      void [_path, _options, _successCallback, _errorCallback];
      throw new Error('Not implemented in this test fixture.');
    },
    getFile(
      _path?: string | null,
      _options?: FileSystemFlags,
      _successCallback?: FileSystemEntryCallback,
      _errorCallback?: ErrorCallback,
    ) {
      void [_path, _options, _successCallback, _errorCallback];
      throw new Error('Not implemented in this test fixture.');
    },
    createReader: () => {
      let delivered = false;
      return {
        readEntries(success: FileSystemEntriesCallback) {
          success(delivered ? [] : children);
          delivered = true;
        },
      };
    },
  };
}

function folderFile(path: string, size = 4): File {
  const file = new File(['x'.repeat(size)], path.split('/').at(-1) ?? path);
  Object.defineProperty(file, 'webkitRelativePath', { value: path });
  return file;
}

void test('folder selections preserve browser relative paths and infer the root name', () => {
  const files = [
    folderFile('sub-001/mri/orig.mgz'),
    folderFile('sub-001/stats/aseg.stats'),
  ];

  assert.equal(uploadRelativePath(files[0]), 'sub-001/mri/orig.mgz');
  assert.equal(inferredUploadName(files), 'sub-001');
  assert.deepEqual(summarizeUploadSelection(files), {
    sourceKind: 'folder',
    sourceName: 'sub-001',
    fileCount: 2,
    totalBytes: 8,
    detectedFormat: 'freesurfer',
    subjectIds: [],
  });
});

void test('known FastSurfer and BIDS layouts are identified conservatively', () => {
  assert.equal(summarizeUploadSelection([
    folderFile('subject/mri/orig.mgz'),
    folderFile('subject/mri/aparc.DKTatlas+aseg.deep.mgz'),
    folderFile('subject/stats/aseg.stats'),
  ]).detectedFormat, 'fastsurfer');

  assert.equal(summarizeUploadSelection([
    folderFile('study/dataset_description.json'),
    folderFile('study/sub-01/anat/sub-01_T1w.nii.gz'),
  ]).detectedFormat, 'bids');
});

void test('arbitrary single files remain valid generic upload selections', () => {
  const file = new File(['notes'], 'analysis.custom-format');
  assert.equal(uploadRelativePath(file), 'analysis.custom-format');
  assert.deepEqual(summarizeUploadSelection([file]), {
    sourceKind: 'files',
    sourceName: 'analysis.custom-format',
    fileCount: 1,
    totalBytes: 5,
    detectedFormat: null,
    subjectIds: [],
  });
});

void test('multi-subject datasets report the cases that will be created', () => {
  assert.deepEqual(summarizeUploadSelection([
    folderFile('study/dataset_description.json'),
    folderFile('study/sub-02/anat/sub-02_T1w.nii.gz'),
    folderFile('study/sub-01/anat/sub-01_T1w.nii.gz'),
  ]).subjectIds, ['sub-01', 'sub-02']);

  assert.deepEqual(summarizeUploadSelection([
    folderFile('subjects/bert/mri/orig.mgz'),
    folderFile('subjects/bert/stats/aseg.stats'),
    folderFile('subjects/ernie/mri/orig.mgz'),
    folderFile('subjects/ernie/stats/aseg.stats'),
  ]).subjectIds, ['bert', 'ernie']);
});

void test('folder drops are traversed recursively and retain their hierarchy', async () => {
  const root = directoryEntry('subject', [
    directoryEntry('subject/mri', [fileEntry('subject/mri/orig.mgz', 'volume')]),
    directoryEntry('subject/stats', [fileEntry('subject/stats/aseg.stats', 'stats')]),
  ]);
  const transfer = {
    items: [{ webkitGetAsEntry: () => root }],
    files: [],
  } as unknown as DataTransfer;

  const files = await droppedUploadFiles(transfer, 30_000);
  assert.deepEqual(files.map(uploadRelativePath), [
    'subject/mri/orig.mgz',
    'subject/stats/aseg.stats',
  ]);
});

void test('folder drops enforce the configured file limit', async () => {
  const root = directoryEntry('subject', [
    fileEntry('subject/one.mgz', 'one'),
    fileEntry('subject/two.mgz', 'two'),
  ]);
  const transfer = {
    items: [{ webkitGetAsEntry: () => root }],
    files: [],
  } as unknown as DataTransfer;

  await assert.rejects(droppedUploadFiles(transfer, 1), /limited to 1 files/);
});
