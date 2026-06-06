import { useCallback, useState, type ChangeEvent } from 'react';

import { inferCaseName } from '../utils/caseNames';
import type { CaseMetadataInput } from '../utils/api/cases';

interface UseCaseUploadModalOptions {
  isBusy?: boolean;
  onCreateNewCaseUpload: (files: File[], caseName: string, metadata?: CaseMetadataInput) => Promise<void>;
  onAddToCaseUpload?: (files: File[]) => Promise<void>;
}

export function useCaseUploadModal({
  isBusy = false,
  onCreateNewCaseUpload,
  onAddToCaseUpload,
}: UseCaseUploadModalOptions) {
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [pendingUploadDefaultName, setPendingUploadDefaultName] = useState('');
  const [showUploadModal, setShowUploadModal] = useState(false);

  const selectUploadFiles = useCallback((files: File[]) => {
    if (files.length === 0) {
      return;
    }
    setPendingUploadFiles(files);
    setPendingUploadDefaultName(inferCaseName(files[0].name));
    setShowUploadModal(true);
  }, []);

  const resetUploadModal = useCallback(() => {
    setShowUploadModal(false);
    setPendingUploadFiles([]);
    setPendingUploadDefaultName('');
  }, []);

  const requestUploadFile = useCallback(() => {
    if (isBusy) {
      return;
    }
    setPendingUploadFiles([]);
    setPendingUploadDefaultName('');
    setShowUploadModal(true);
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

  const confirmCreateNewCaseUpload = useCallback(async (caseName: string, metadata?: CaseMetadataInput) => {
    const files = pendingUploadFiles;
    if (files.length === 0) {
      throw new Error('No MRI file selected.');
    }
    await onCreateNewCaseUpload(files, caseName, metadata);
    resetUploadModal();
  }, [onCreateNewCaseUpload, pendingUploadFiles, resetUploadModal]);

  const confirmAddToCaseUpload = useCallback(async () => {
    if (!onAddToCaseUpload) {
      throw new Error('Adding to the current case is not available here.');
    }
    const files = pendingUploadFiles;
    if (files.length === 0) {
      throw new Error('No MRI file selected.');
    }
    await onAddToCaseUpload(files);
    resetUploadModal();
  }, [onAddToCaseUpload, pendingUploadFiles, resetUploadModal]);

  return {
    pendingUploadFiles,
    pendingUploadDefaultName,
    showUploadModal,
    requestUploadFile,
    handleFileSelection,
    selectUploadFiles,
    closeUploadModal,
    confirmCreateNewCaseUpload,
    confirmAddToCaseUpload: onAddToCaseUpload ? confirmAddToCaseUpload : undefined,
  };
}
