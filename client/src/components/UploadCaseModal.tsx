import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, FileUp, X } from 'lucide-react';
import { useFrontendConfig } from '../auth/frontendConfigContext';
import type { CaseMetadataInput } from '../utils/api/cases';
import type { LinkedDirectorySelection } from '../utils/api/cases';
import { getCaseNameValidationError } from '../utils/caseNames';
import { droppedUploadFiles, summarizeUploadSelection } from '../utils/uploadFiles';

interface UploadCaseModalProps {
  selectedFiles: File[];
  linkedSource?: LinkedDirectorySelection | null;
  defaultName: string;
  addToCaseLabel?: string | null;
  onClose: () => void;
  onSelectFiles: (files: File[]) => void;
  onClearSelection: () => void;
  onSelectLinkedDirectory?: () => Promise<void> | void;
  onCreateNewCase: (
    caseName: string,
    metadata?: CaseMetadataInput,
    importMode?: 'single' | 'dataset',
  ) => Promise<void> | void;
  onAddToCase?: () => Promise<void> | void;
}

const DETECTED_FORMAT_LABELS = {
  bids: 'BIDS dataset detected',
  fastsurfer: 'FastSurfer case structure detected',
  freesurfer: 'FreeSurfer case structure detected',
  dicom: 'DICOM data detected',
} as const;

function formatByteCount(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

export function UploadCaseModal({
  selectedFiles,
  linkedSource = null,
  defaultName,
  addToCaseLabel = null,
  onClose,
  onSelectFiles,
  onClearSelection,
  onSelectLinkedDirectory,
  onCreateNewCase,
  onAddToCase,
}: UploadCaseModalProps) {
  const { max_upload_files: maxUploadFiles } = useFrontendConfig();
  const filename = selectedFiles[0]?.name ?? null;
  const fileCount = selectedFiles.length;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const [caseName, setCaseName] = useState(defaultName);
  const [description, setDescription] = useState('');
  const [modalities, setModalities] = useState<string[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [customTag, setCustomTag] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [optionalDetailsOpen, setOptionalDetailsOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [transferMode, setTransferMode] = useState<'upload' | 'link'>(linkedSource ? 'link' : 'upload');
  const addToCaseEnabled = typeof onAddToCase === 'function';
  const [mode, setMode] = useState<'current' | 'new' | 'dataset'>(addToCaseEnabled ? 'current' : 'new');
  useEffect(() => {
    if (linkedSource) setMode('new');
  }, [linkedSource]);
  useEffect(() => {
    setTransferMode(linkedSource ? 'link' : 'upload');
    setPickerOpen(false);
  }, [linkedSource, selectedFiles.length]);
  const selectionSummary = selectedFiles.length > 0 ? summarizeUploadSelection(selectedFiles) : null;
  const batchSubjectIds = selectionSummary?.subjectIds ?? linkedSource?.summary?.subject_ids ?? [];
  const isBatchImport = batchSubjectIds.length > 0;
  useEffect(() => {
    if (isBatchImport) {
      setMode('dataset');
    } else {
      setMode((currentMode) => currentMode === 'dataset' ? (addToCaseEnabled ? 'current' : 'new') : currentMode);
    }
  }, [addToCaseEnabled, isBatchImport]);
  const modalityOptions = ['T1w', 'FLAIR', 'DWI', 'rs-fMRI', 'PET'];
  const tagOptions = ['control', 'clinical', 'research', 'urgent'];

  const hasSelectedFiles = linkedSource !== null || Boolean(filename && fileCount > 0);
  const selectedFileLabel = linkedSource?.name ?? (selectionSummary?.sourceKind === 'folder'
    ? selectionSummary.sourceName ?? 'Selected folder'
    : filename ? (fileCount > 1 ? `${fileCount} files selected` : filename) : '');
  const selectedFileDetail = selectionSummary
    ? `${selectionSummary.fileCount} ${selectionSummary.fileCount === 1 ? 'file' : 'files'} · ${formatByteCount(selectionSummary.totalBytes)}`
    : linkedSource
      ? `${linkedSource.summary?.file_count ? `${linkedSource.summary.file_count} files · ` : ''}Read-only · files stay in place`
      : `${fileCount} ${fileCount === 1 ? 'file' : 'files'}`;

  const chooseFiles = () => {
    if (loading) return;
    fileInputRef.current?.click();
  };

  const chooseFolder = () => {
    if (loading) return;
    folderInputRef.current?.click();
  };

  const chooseLinkedFolder = async () => {
    if (loading || !onSelectLinkedDirectory) return;
    try {
      setError(null);
      await onSelectLinkedDirectory();
    } catch (linkError: unknown) {
      setError(linkError instanceof Error ? linkError.message : 'Could not link the selected folder.');
    }
  };

  const handleFiles = (files: File[]) => {
    if (files.length > maxUploadFiles) {
      setError(`Selections are limited to ${maxUploadFiles.toLocaleString()} files.`);
      return;
    }
    const selected = files.filter(Boolean);
    if (selected.length === 0) return;
    setError(null);
    onSelectFiles(selected);
  };

  const handleCreateNewCase = async () => {
    const trimmed = caseName.trim();
    const requiresCaseName = mode !== 'dataset';
    if (!hasSelectedFiles) {
      setError('Choose files or a folder first.');
      return;
    }
    if (requiresCaseName && !trimmed) {
      setError('Case name cannot be empty.');
      return;
    }
    const validationError = requiresCaseName ? getCaseNameValidationError(trimmed) : null;
    if (validationError) {
      setError(validationError);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      await onCreateNewCase(trimmed || defaultName, {
        description: description.trim() || null,
        modalities,
        tags,
        notes: notes.trim() || null,
      }, mode === 'dataset' ? 'dataset' : 'single');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err) || 'Upload failed');
      setLoading(false);
    }
  };

  const toggleListValue = (value: string, current: string[], setCurrent: (next: string[]) => void, requireOne = false) => {
    if (current.includes(value)) {
      if (requireOne && current.length === 1) return;
      setCurrent(current.filter((entry) => entry !== value));
      return;
    }
    setCurrent([...current, value]);
  };

  const addCustomTag = () => {
    const next = customTag.trim().toLowerCase().replace(/\s+/g, '-');
    if (!next) return;
    if (!tags.includes(next)) {
      setTags([...tags, next]);
    }
    setCustomTag('');
  };

  const handleAddToCase = async () => {
    if (!onAddToCase) {
      return;
    }
    if (!hasSelectedFiles) {
      setError('Choose files or a folder first.');
      return;
    }
    try {
      setLoading(true);
      setError(null);
      await onAddToCase();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err) || 'Upload failed');
      setLoading(false);
    }
  };

  const modeClass = (active: boolean) => `nc-upload-dialog-mode${active ? ' is-active' : ''}`;

  const activateSourcePicker = () => {
    if (loading) return;
    if (transferMode === 'link') {
      void chooseLinkedFolder();
      return;
    }
    setPickerOpen((open) => !open);
  };

  return (
    <div className="nc-upload-dialog-backdrop" onClick={!loading ? onClose : undefined}>
      <div className="nc-upload-dialog" onClick={(event) => event.stopPropagation()}>
        <div className="nc-upload-dialog-header">
          <div className="nc-upload-dialog-title">
            <FileUp size={14} className="text-[var(--nc-warning)]" />
            <h3>Add Data</h3>
          </div>
          <button type="button" onClick={onClose} disabled={loading} className="nc-upload-dialog-close" aria-label="Close upload dialog">
            <X size={16} />
          </button>
        </div>

        <div className="nc-upload-dialog-body">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(event) => {
              handleFiles(Array.from(event.target.files ?? []));
              event.target.value = '';
            }}
          />
          <input
            ref={(element) => {
              folderInputRef.current = element;
              element?.setAttribute('webkitdirectory', '');
              element?.setAttribute('directory', '');
            }}
            type="file"
            multiple
            className="hidden"
            aria-label="Choose folder"
            onChange={(event) => {
              handleFiles(Array.from(event.target.files ?? []));
              event.target.value = '';
            }}
          />

          {onSelectLinkedDirectory && (
            <div className="nc-upload-dialog-transfer" aria-label="Transfer method">
              <button
                type="button"
                className={transferMode === 'upload' ? 'is-active' : ''}
                onClick={() => { setTransferMode('upload'); setPickerOpen(false); }}
                disabled={loading || hasSelectedFiles}
              >
                Upload
              </button>
              <button
                type="button"
                className={transferMode === 'link' ? 'is-active' : ''}
                onClick={() => { setTransferMode('link'); setPickerOpen(false); }}
                disabled={loading || hasSelectedFiles}
              >
                Link
              </button>
            </div>
          )}

          <div
            role="button"
            tabIndex={0}
            data-testid="upload-file-dropzone"
            className={`nc-upload-dialog-dropzone nc-upload-dialog-dropzone-compact ${dragOver ? 'is-drag-over' : ''}`}
            onClick={activateSourcePicker}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                activateSourcePicker();
              }
            }}
            onDragOver={(event) => {
              event.preventDefault();
              if (transferMode === 'upload') setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragOver(false);
              if (transferMode !== 'upload') return;
              void droppedUploadFiles(event.dataTransfer, maxUploadFiles)
                .then(handleFiles)
                .catch((dropError: unknown) => {
                  setError(dropError instanceof Error ? dropError.message : 'Could not read the dropped folder.');
                });
            }}
          >
            <FileUp size={22} className="nc-upload-dialog-dropzone-icon" />
            <div className="nc-upload-dialog-dropzone-copy">
              <span className="nc-upload-dialog-dropzone-title">
                {transferMode === 'link' ? 'Choose a folder to link' : 'Drop files or a folder'}
              </span>
              <span className="nc-upload-dialog-dropzone-action">
                {transferMode === 'link' ? 'Click to choose · files stay in place' : 'Click to select'}
              </span>
              {pickerOpen && transferMode === 'upload' && (
                <span className="nc-upload-dialog-picker-menu" onClick={(event) => event.stopPropagation()}>
                  <button type="button" onClick={chooseFiles}>Select files</button>
                  <button type="button" onClick={chooseFolder}>Select folder</button>
                </span>
              )}
            </div>
          </div>

          {hasSelectedFiles && (
            <>
              <div className="nc-upload-dialog-file is-selected">
                <div className="nc-upload-dialog-file-icon">
                  <Check size={14} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="nc-upload-dialog-file-name">{selectedFileLabel}</div>
                  <div className="nc-upload-dialog-file-types">
                    {linkedSource ? 'Linked folder' : selectionSummary?.sourceKind === 'folder' ? 'Folder' : 'Files'} · {selectedFileDetail}
                  </div>
                  {selectionSummary?.detectedFormat && (
                    <div className="nc-upload-dialog-detected-format">
                      {DETECTED_FORMAT_LABELS[selectionSummary.detectedFormat]}
                    </div>
                  )}
                  {!selectionSummary?.detectedFormat && linkedSource?.summary?.format && linkedSource.summary.format !== 'generic' && (
                    <div className="nc-upload-dialog-detected-format">
                      {DETECTED_FORMAT_LABELS[linkedSource.summary.format as keyof typeof DETECTED_FORMAT_LABELS] ?? `${linkedSource.summary.format} structure detected`}
                    </div>
                  )}
                </div>
                <button type="button" onClick={onClearSelection} disabled={loading} className="nc-upload-dialog-file-change">
                  Change
                </button>
              </div>

              <div className="nc-upload-dialog-section">
                <div className="nc-eyebrow">Import as</div>
                <div className={`nc-upload-dialog-mode-row ${addToCaseEnabled ? 'has-three' : ''}`}>
                  {addToCaseEnabled && (
                    <button type="button" className={modeClass(mode === 'current')} onClick={() => setMode('current')} disabled={loading || Boolean(linkedSource)}>
                      <span className="nc-upload-dialog-radio" aria-hidden="true" />
                      <span className="min-w-0">
                        <span className="nc-upload-dialog-mode-title">Current case</span>
                        <span className="nc-upload-dialog-mode-subtitle">{addToCaseLabel ?? 'Add these files'}</span>
                      </span>
                    </button>
                  )}
                  <button type="button" className={modeClass(mode === 'new')} onClick={() => setMode('new')} disabled={loading}>
                    <span className="nc-upload-dialog-radio" aria-hidden="true" />
                    <span className="min-w-0">
                      <span className="nc-upload-dialog-mode-title">One new case</span>
                      <span className="nc-upload-dialog-mode-subtitle">Keep the selection together</span>
                    </span>
                  </button>
                  <button type="button" className={modeClass(mode === 'dataset')} onClick={() => setMode('dataset')} disabled={loading || !isBatchImport}>
                    <span className="nc-upload-dialog-radio" aria-hidden="true" />
                    <span className="min-w-0">
                      <span className="nc-upload-dialog-mode-title">Dataset</span>
                      <span className="nc-upload-dialog-mode-subtitle">
                        {isBatchImport ? `${batchSubjectIds.length} subjects → ${batchSubjectIds.length} cases` : 'Available when subjects are detected'}
                      </span>
                    </span>
                  </button>
                </div>
              </div>

              {mode === 'dataset' && (
                <div className="nc-upload-dialog-dataset-summary">
                  <div className="nc-upload-dialog-dataset-title">{batchSubjectIds.length} cases will be created</div>
                  <div className="nc-upload-dialog-dataset-subtitle">Subject IDs were detected from the folder structure.</div>
                  <div className="nc-upload-dialog-subject-list">
                    {batchSubjectIds.slice(0, 8).map((subjectId) => <span key={subjectId}>{subjectId}</span>)}
                    {batchSubjectIds.length > 8 && <span>+{batchSubjectIds.length - 8} more</span>}
                  </div>
                </div>
              )}

              {mode === 'new' && (
                <div className="nc-upload-dialog-form">
                  <label className="nc-upload-dialog-field">
                    <span className="nc-eyebrow">Case name</span>
                    <input
                      data-testid="upload-case-name-input"
                      autoFocus
                      type="text"
                      value={caseName}
                      onChange={(event) => setCaseName(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          void handleCreateNewCase();
                        }
                      }}
                      disabled={loading}
                      className="nc-upload-dialog-input"
                      placeholder="e.g. sub-302"
                    />
                  </label>
                </div>
              )}

              {(mode === 'new' || mode === 'dataset') && (
                <div className="nc-upload-dialog-optional">
                  <button
                    type="button"
                    className="nc-upload-dialog-optional-trigger"
                    onClick={() => setOptionalDetailsOpen((open) => !open)}
                    aria-expanded={optionalDetailsOpen}
                  >
                    <ChevronDown size={13} className={optionalDetailsOpen ? 'is-open' : ''} aria-hidden="true" />
                    Optional details
                  </button>
                  {optionalDetailsOpen && (
                    <div className="nc-upload-dialog-form">
                      <label className="nc-upload-dialog-field">
                        <span className="nc-eyebrow">Description</span>
                        <input
                          type="text"
                          value={description}
                          onChange={(event) => setDescription(event.target.value)}
                          disabled={loading}
                          className="nc-upload-dialog-input"
                          placeholder={mode === 'dataset' ? 'Applied to every case' : 'Age, sex, clinical context...'}
                        />
                      </label>
                  <div className="nc-upload-dialog-field">
                    <div className="nc-eyebrow">Modalities</div>
                    <div className="nc-upload-dialog-chip-row">
                      {modalityOptions.map((option) => {
                        const active = modalities.includes(option);
                        return (
                          <button
                            key={option}
                            type="button"
                            onClick={() => toggleListValue(option, modalities, setModalities)}
                            disabled={loading}
                            className={`nc-chip transition ${active ? 'nc-chip-blue' : 'hover:text-[var(--nc-tx)]'}`}
                          >
                            {option}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className="nc-upload-dialog-field">
                    <div className="nc-eyebrow">Tags</div>
                    <div className="nc-upload-dialog-chip-row">
                      {[...tagOptions, ...tags.filter((tag) => !tagOptions.includes(tag))].map((option) => {
                        const active = tags.includes(option);
                        return (
                          <button
                            key={option}
                            type="button"
                            onClick={() => toggleListValue(option, tags, setTags)}
                            disabled={loading}
                            className={`nc-chip transition ${active ? 'nc-chip-green' : 'hover:text-[var(--nc-tx)]'}`}
                          >
                            {option}
                          </button>
                        );
                      })}
                      <input
                        type="text"
                        value={customTag}
                        onChange={(event) => setCustomTag(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') {
                            event.preventDefault();
                            addCustomTag();
                          }
                        }}
                        disabled={loading}
                        className="nc-upload-dialog-tag-input"
                        placeholder="custom tag"
                      />
                      <button
                        type="button"
                        onClick={addCustomTag}
                        disabled={loading || !customTag.trim()}
                        className="nc-btn nc-upload-dialog-add-tag"
                      >
                        add
                      </button>
                    </div>
                  </div>

                  <label className="nc-upload-dialog-field">
                    <span className="nc-eyebrow">Notes</span>
                    <textarea
                      value={notes}
                      onChange={(event) => setNotes(event.target.value)}
                      disabled={loading}
                      rows={3}
                      className="nc-upload-dialog-input nc-upload-dialog-textarea"
                      placeholder="QC notes, findings, or cohort context..."
                    />
                  </label>
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {error && (
            <div className="nc-upload-dialog-error">
              {error}
            </div>
          )}
        </div>

        <div className="nc-upload-dialog-footer">
          <div className="nc-upload-dialog-actions">
            <button type="button" onClick={onClose} disabled={loading} className="nc-btn">
              Cancel
            </button>
            {hasSelectedFiles && (mode === 'current' ? (
              <button
                type="button"
                data-testid="confirm-add-to-case"
                onClick={() => void handleAddToCase()}
                disabled={loading}
                className="nc-btn nc-btn-active"
              >
                {loading ? 'Uploading…' : 'Add to Case'}
              </button>
            ) : (
              <button
                type="button"
                data-testid="confirm-upload-case"
                onClick={() => void handleCreateNewCase()}
                disabled={loading || (mode === 'new' && !caseName.trim())}
                className="nc-btn nc-btn-warning"
              >
                {loading ? 'Uploading…' : mode === 'dataset' ? `Create ${batchSubjectIds.length} Cases` : 'Create Case'}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
