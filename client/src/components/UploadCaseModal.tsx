import { useRef, useState } from 'react';
import { Check, FileUp, X } from 'lucide-react';
import type { CaseMetadataInput } from '../utils/api/cases';
import { getCaseNameValidationError } from '../utils/caseNames';

interface UploadCaseModalProps {
  isOpen: boolean;
  filename: string | null;
  fileCount?: number;
  defaultName: string;
  addToCaseLabel?: string | null;
  onClose: () => void;
  onSelectFiles?: (files: File[]) => void;
  onCreateNewCase: (caseName: string, metadata?: CaseMetadataInput) => Promise<void> | void;
  onAddToCase?: () => Promise<void> | void;
}

const FORMAT_LABELS = ['.nii', '.nii.gz', '.mgz', '.mgh', '.dcm', '.zip'];

export function UploadCaseModal({
  isOpen,
  filename,
  fileCount = 1,
  defaultName,
  addToCaseLabel = null,
  onClose,
  onSelectFiles,
  onCreateNewCase,
  onAddToCase,
}: UploadCaseModalProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [caseName, setCaseName] = useState(defaultName);
  const [description, setDescription] = useState('');
  const [modalities, setModalities] = useState<string[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [customTag, setCustomTag] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const addToCaseEnabled = typeof onAddToCase === 'function';
  const [mode, setMode] = useState<'current' | 'new'>(addToCaseEnabled ? 'current' : 'new');
  const modalityOptions = ['T1w', 'FLAIR', 'DWI', 'rs-fMRI', 'PET'];
  const tagOptions = ['control', 'clinical', 'research', 'urgent'];

  if (!isOpen) return null;

  const hasSelectedFiles = Boolean(filename && fileCount > 0);
  const selectedFileLabel = filename ? (fileCount > 1 ? `${fileCount} files selected` : filename) : '';

  const chooseFiles = () => {
    if (loading) return;
    fileInputRef.current?.click();
  };

  const handleFiles = (files: File[]) => {
    const selected = files.filter(Boolean);
    if (selected.length === 0) return;
    setError(null);
    onSelectFiles?.(selected);
  };

  const handleCreateNewCase = async () => {
    const trimmed = caseName.trim();
    if (!hasSelectedFiles) {
      setError('Choose an MRI file or DICOM archive first.');
      return;
    }
    if (!trimmed) {
      setError('Case name cannot be empty.');
      return;
    }
    const validationError = getCaseNameValidationError(trimmed);
    if (validationError) {
      setError(validationError);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      await onCreateNewCase(trimmed, {
        description: description.trim() || null,
        modalities,
        tags,
        notes: notes.trim() || null,
      });
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
      setError('Choose an MRI file or DICOM archive first.');
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

  return (
    <div className="nc-upload-dialog-backdrop" onClick={!loading ? onClose : undefined}>
      <div className="nc-upload-dialog" onClick={(event) => event.stopPropagation()}>
        <div className="nc-upload-dialog-header">
          <div className="nc-upload-dialog-title">
            <FileUp size={14} className="text-[var(--nc-warning)]" />
            <h3>{hasSelectedFiles ? 'Upload MRI' : 'New Upload'}</h3>
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

          <div className="nc-upload-dialog-steps" aria-label="Upload progress">
            {['Upload MRI', addToCaseEnabled ? 'Destination' : 'Case Details'].map((label, index) => {
              const uploadStep = index === 0;
              const done = uploadStep && hasSelectedFiles;
              const active = uploadStep ? !hasSelectedFiles : hasSelectedFiles;
              return (
                <div key={label} className="nc-upload-dialog-step-wrap">
                  <div className="nc-upload-dialog-step">
                    <span className={`nc-upload-dialog-step-dot ${done ? 'is-done' : active ? 'is-active' : ''}`}>
                      {done ? <Check size={10} /> : index + 1}
                    </span>
                    <span className={`nc-upload-dialog-step-label ${active || done ? 'is-active' : ''}`}>{label}</span>
                  </div>
                  {index === 0 && <span className="nc-upload-dialog-step-line" />}
                </div>
              );
            })}
          </div>

          {!hasSelectedFiles ? (
            <button
              type="button"
              data-testid="upload-file-dropzone"
              className={`nc-upload-dialog-dropzone ${dragOver ? 'is-drag-over' : ''}`}
              onClick={chooseFiles}
              onDragOver={(event) => {
                event.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragOver(false);
                handleFiles(Array.from(event.dataTransfer.files ?? []));
              }}
            >
              <FileUp size={28} className="nc-upload-dialog-dropzone-icon" />
              <span className="nc-upload-dialog-dropzone-title">
                Drag & drop or <span>browse files</span>
              </span>
              <span className="nc-upload-dialog-dropzone-formats">{FORMAT_LABELS.join('  ·  ')}</span>
              <span className="nc-upload-dialog-dropzone-note">Processed locally</span>
            </button>
          ) : (
            <>
              <div className="nc-upload-dialog-file is-selected">
                <div className="nc-upload-dialog-file-icon">
                  <Check size={14} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="nc-upload-dialog-file-name">{selectedFileLabel}</div>
                  <div className="nc-upload-dialog-file-types">
                    {FORMAT_LABELS.join(' · ')}
                  </div>
                </div>
                {onSelectFiles && (
                  <button type="button" onClick={chooseFiles} disabled={loading} className="nc-upload-dialog-file-change">
                    Change
                  </button>
                )}
              </div>

              {addToCaseEnabled && (
                <div className="nc-upload-dialog-section">
                  <div className="nc-eyebrow">Add As</div>
                  <div className="nc-upload-dialog-mode-row">
                    <button type="button" className={modeClass(mode === 'current')} onClick={() => setMode('current')} disabled={loading}>
                      <span className="nc-upload-dialog-radio" aria-hidden="true" />
                      <span className="min-w-0">
                        <span className="nc-upload-dialog-mode-title">Add to current case</span>
                        <span className="nc-upload-dialog-mode-subtitle">{addToCaseLabel ?? 'New volume or overlay'}</span>
                      </span>
                    </button>
                    <button type="button" className={modeClass(mode === 'new')} onClick={() => setMode('new')} disabled={loading}>
                      <span className="nc-upload-dialog-radio" aria-hidden="true" />
                      <span className="min-w-0">
                        <span className="nc-upload-dialog-mode-title">New case</span>
                        <span className="nc-upload-dialog-mode-subtitle">Separate subject</span>
                      </span>
                    </button>
                  </div>
                </div>
              )}

              {mode === 'new' && (
                <div className="nc-upload-dialog-form">
                  <label className="nc-upload-dialog-field">
                    <span className="nc-eyebrow">Subject ID</span>
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

                  <label className="nc-upload-dialog-field">
                    <span className="nc-eyebrow">Description</span>
                    <input
                      type="text"
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                      disabled={loading}
                      className="nc-upload-dialog-input"
                      placeholder="Age, sex, clinical notes..."
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
            </>
          )}

          {error && (
            <div className="nc-upload-dialog-error">
              {error}
            </div>
          )}
        </div>

        <div className="nc-upload-dialog-footer">
          <div className="nc-upload-dialog-note">NIfTI, DICOM and FreeSurfer outputs supported.</div>
          <div className="nc-upload-dialog-actions">
            <button type="button" onClick={onClose} disabled={loading} className="nc-btn">
              Cancel
            </button>
            {hasSelectedFiles && (addToCaseEnabled && mode === 'current' ? (
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
                disabled={loading || !caseName.trim()}
                className="nc-btn nc-btn-warning"
              >
                {loading ? 'Uploading…' : addToCaseEnabled ? 'Create New Case' : 'Add to Workspace'}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
