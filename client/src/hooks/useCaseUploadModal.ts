import { useCallback, useState, type ChangeEvent } from 'react';

import { inferCaseName } from '../utils/caseNames';
import type { CaseMetadataInput, LinkedDirectorySelection } from '../utils/api/cases';
import { inferredUploadName } from '../utils/uploadFiles';

interface UseCaseUploadModalOptions {
  isBusy?: boolean;
  onCreateNewCaseUpload: (files: File[], caseName: string, metadata?: CaseMetadataInput, importMode?: 'single' | 'dataset') => Promise<void>;
  onCreateNewCaseLink?: (source: LinkedDirectorySelection, caseName: string, metadata?: CaseMetadataInput, importMode?: 'single' | 'dataset') => Promise<void>;
  onAddToCaseUpload?: (files: File[]) => Promise<void>;
}

export function useCaseUploadModal({
  isBusy = false,
  onCreateNewCaseUpload,
  onCreateNewCaseLink,
  onAddToCaseUpload,
}: UseCaseUploadModalOptions) {
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [pendingUploadDefaultName, setPendingUploadDefaultName] = useState('');
  const [pendingLinkedSource, setPendingLinkedSource] = useState<LinkedDirectorySelection | null>(null);
  const [showUploadModal, setShowUploadModal] = useState(false);

  const selectUploadFiles = useCallback((files: File[]) => {
    if (files.length === 0) {
      return;
    }
    setPendingUploadFiles(files);
    setPendingLinkedSource(null);
    setPendingUploadDefaultName(inferCaseName(inferredUploadName(files)));
    setShowUploadModal(true);
  }, []);

  const resetUploadModal = useCallback(() => {
    setShowUploadModal(false);
    setPendingUploadFiles([]);
    setPendingUploadDefaultName('');
    setPendingLinkedSource(null);
  }, []);

  const requestUploadFile = useCallback(() => {
    if (isBusy) {
      return;
    }
    setPendingUploadFiles([]);
    setPendingUploadDefaultName('');
    setShowUploadModal(true);
  }, [isBusy]);

  const selectLinkedDirectory = useCallback(async () => {
    if (isBusy || !window.neurocadeElectron?.selectLinkedDirectory) return;
    const source = await window.neurocadeElectron.selectLinkedDirectory();
    if (!source) return;
    setPendingUploadFiles([]);
    setPendingLinkedSource(source);
    setPendingUploadDefaultName(source.name);
    setShowUploadModal(true);
  }, [isBusy]);

  const clearPendingSelection = useCallback(() => {
    if (isBusy) return;
    setPendingUploadFiles([]);
    setPendingLinkedSource(null);
    setPendingUploadDefaultName('');
  }, [isBusy]);

  const handleFileSelection = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    selectUploadFiles(files);
  }, [selectUploadFiles]);

  const closeUploadModal = useCallback(() => {
    if (isBusy) {
      return;
    }
    resetUploadModal();
  }, [isBusy, resetUploadModal]);

  const confirmCreateNewCaseUpload = useCallback(async (
    caseName: string,
    metadata?: CaseMetadataInput,
    importMode: 'single' | 'dataset' = 'single',
  ) => {
    if (pendingLinkedSource) {
      if (!onCreateNewCaseLink) throw new Error('Linking directories is not available here.');
      await onCreateNewCaseLink(pendingLinkedSource, caseName, metadata, importMode);
    } else {
      const files = pendingUploadFiles;
      if (files.length === 0) throw new Error('No files selected.');
      await onCreateNewCaseUpload(files, caseName, metadata, importMode);
    }
    resetUploadModal();
  }, [onCreateNewCaseLink, onCreateNewCaseUpload, pendingLinkedSource, pendingUploadFiles, resetUploadModal]);

  const confirmAddToCaseUpload = useCallback(async () => {
    if (!onAddToCaseUpload) {
      throw new Error('Adding to the current case is not available here.');
    }
    const files = pendingUploadFiles;
    if (files.length === 0) {
      throw new Error('No files selected.');
    }
    await onAddToCaseUpload(files);
    resetUploadModal();
  }, [onAddToCaseUpload, pendingUploadFiles, resetUploadModal]);

  return {
    pendingUploadFiles,
    pendingLinkedSource,
    pendingUploadDefaultName,
    showUploadModal,
    requestUploadFile,
    handleFileSelection,
    selectUploadFiles,
    clearPendingSelection,
    selectLinkedDirectory: window.neurocadeElectron?.selectLinkedDirectory ? selectLinkedDirectory : undefined,
    closeUploadModal,
    confirmCreateNewCaseUpload,
    confirmAddToCaseUpload: onAddToCaseUpload ? confirmAddToCaseUpload : undefined,
  };
}
