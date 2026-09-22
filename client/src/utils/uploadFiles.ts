export type DetectedUploadFormat = 'bids' | 'fastsurfer' | 'freesurfer' | 'dicom' | null;

export interface UploadSelectionSummary {
  sourceKind: 'files' | 'folder';
  sourceName: string | null;
  fileCount: number;
  totalBytes: number;
  detectedFormat: DetectedUploadFormat;
  subjectIds: string[];
}

type FileWithRelativePath = File & { webkitRelativePath?: string };

function withRelativePath(file: File, relativePath: string): File {
  const copy = new File([file], file.name, { type: file.type, lastModified: file.lastModified });
  Object.defineProperty(copy, 'webkitRelativePath', { value: relativePath, enumerable: true });
  return copy;
}

async function readDirectoryEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  const entries: FileSystemEntry[] = [];
  while (true) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (batch.length === 0) return entries;
    entries.push(...batch);
  }
}

async function collectEntryFiles(entry: FileSystemEntry, files: File[], maxFiles: number): Promise<void> {
  if (entry.isFile) {
    if (files.length >= maxFiles) {
      throw new Error(`Selections are limited to ${maxFiles.toLocaleString()} files.`);
    }
    const fileEntry = entry as FileSystemFileEntry;
    const file = await new Promise<File>((resolve, reject) => fileEntry.file(resolve, reject));
    files.push(withRelativePath(file, entry.fullPath.replace(/^\/+/, '')));
    return;
  }
  if (entry.isDirectory) {
    const children = await readDirectoryEntries((entry as FileSystemDirectoryEntry).createReader());
    for (const child of children) await collectEntryFiles(child, files, maxFiles);
  }
}

/** Preserve directory hierarchy when Chromium supplies filesystem entries for a drop. */
export async function droppedUploadFiles(dataTransfer: DataTransfer, maxFiles: number): Promise<File[]> {
  const entries = Array.from(dataTransfer.items ?? [])
    .map((item) => item.webkitGetAsEntry?.() ?? null)
    .filter((entry): entry is FileSystemEntry => entry !== null);
  if (entries.length === 0) {
    const files = Array.from(dataTransfer.files ?? []);
    if (files.length > maxFiles) throw new Error(`Selections are limited to ${maxFiles.toLocaleString()} files.`);
    return files;
  }

  const files: File[] = [];
  for (const entry of entries) await collectEntryFiles(entry, files, maxFiles);
  return files.sort((left, right) => uploadRelativePath(left).localeCompare(uploadRelativePath(right)));
}

export function uploadRelativePath(file: File): string {
  const relativePath = (file as FileWithRelativePath).webkitRelativePath?.trim();
  return relativePath || file.name;
}

export function summarizeUploadSelection(files: File[]): UploadSelectionSummary {
  const relativePaths = files.map(uploadRelativePath);
  const folderPaths = relativePaths.filter((path) => path.includes('/'));
  const sourceKind = folderPaths.length > 0 ? 'folder' : 'files';
  const sourceName = sourceKind === 'folder'
    ? folderPaths[0]?.split('/')[0] ?? null
    : files.length === 1 ? files[0]?.name ?? null : null;
  const normalizedPaths = relativePaths.map((path) => path.replaceAll('\\', '/').toLowerCase());
  const relativeToRoot = normalizedPaths.map((path) => path.includes('/') ? path.slice(path.indexOf('/') + 1) : path);
  const hasBidsDescription = relativeToRoot.some((path) => path === 'dataset_description.json');
  const hasBidsSubject = relativeToRoot.some((path) => path.startsWith('sub-'));
  const hasMriDirectory = relativeToRoot.some((path) => path.includes('mri/'));
  const hasSubjectOutputs = relativeToRoot.some((path) => path.includes('stats/') || path.includes('surf/') || path.includes('label/'));
  const hasFastSurferMarker = relativeToRoot.some((path) => (
    path.includes('aparc.dktatlas+aseg.deep')
    || path.includes('aseg.auto_noccseg')
    || path.includes('deep-seg')
  ));
  const hasDicom = normalizedPaths.some((path) => path.endsWith('.dcm') || path.endsWith('.dicom'));
  let subjectIds: string[] = [];

  let detectedFormat: DetectedUploadFormat = null;
  if (hasBidsDescription && hasBidsSubject) {
    detectedFormat = 'bids';
    subjectIds = [...new Set(relativeToRoot
      .map((path) => path.split('/')[0])
      .filter((part) => part.startsWith('sub-') && part.length > 4))].sort();
  } else if (hasMriDirectory && hasSubjectOutputs) {
    detectedFormat = hasFastSurferMarker ? 'fastsurfer' : 'freesurfer';
    subjectIds = [...new Set(relativeToRoot.flatMap((path) => {
      const marker = path.indexOf('/mri/');
      return marker > 0 ? [path.slice(0, marker).split('/').at(-1) ?? ''] : [];
    }).filter(Boolean))].sort();
  } else if (hasDicom) {
    detectedFormat = 'dicom';
  }

  return {
    sourceKind,
    sourceName,
    fileCount: files.length,
    totalBytes: files.reduce((total, file) => total + file.size, 0),
    detectedFormat,
    subjectIds,
  };
}

export function inferredUploadName(files: File[]): string {
  const summary = summarizeUploadSelection(files);
  return summary.sourceKind === 'folder' && summary.sourceName
    ? summary.sourceName
    : files[0]?.name ?? '';
}
