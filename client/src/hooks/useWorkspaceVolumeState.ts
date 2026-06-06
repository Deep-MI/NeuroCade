import { useCallback } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import {
  DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
  DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
} from '../constants';
import type { LayerType, Volume } from '../types';
import { appUrl } from '../utils/api';
import { forgetClosedCaseVolume, rememberClosedCaseVolume } from '../utils/caseStorage';
import { layerDisplayName } from '../utils/layerAliases';
import { defaultSurfaceColorModeForLayer } from '../utils/surfaceColors';

function layerTypeOf(volume: Volume): LayerType {
  return volume.type ?? 'intensity';
}

interface UseWorkspaceVolumeStateArgs {
  activeCaseId: string | null;
  initialCaseId: string | null;
  uploadCaseId: string | null;
  isMaskLikeVolume: (filename: string) => boolean;
  setVolumes: Dispatch<SetStateAction<Volume[]>>;
}

interface LoadVolumeCommand {
  downloadPath: string;
  filename: string;
  name?: string;
  type?: string;
  lut?: string;
  customLutDownloadUrl?: string;
  curvatureDownloadUrl?: string;
  annotationDownloadUrl?: string;
  visible?: boolean;
}

export function useWorkspaceVolumeState({
  activeCaseId,
  initialCaseId,
  uploadCaseId,
  isMaskLikeVolume,
  setVolumes,
}: UseWorkspaceVolumeStateArgs) {
  const buildLoadedLayer = useCallback((
    cmd: LoadVolumeCommand,
    layerType: LayerType,
  ): Volume => {
    const base = {
      id: cmd.filename,
      name: layerDisplayName(cmd),
      filename: cmd.filename,
      url: `${appUrl(cmd.downloadPath)}?t=${Date.now()}`,
      opacity: layerType === 'segmentation' ? 0.7 : 1.0,
      colormap: layerType === 'surface' ? 'surface' : layerType === 'segmentation' ? 'jet' : 'gray',
      visible: cmd.visible ?? true,
    };

    if (layerType === 'surface') {
      const surfaceLayer = {
        ...base,
        type: 'surface' as const,
        curvatureUrl: cmd.curvatureDownloadUrl ? appUrl(cmd.curvatureDownloadUrl) : undefined,
        annotationUrl: cmd.annotationDownloadUrl ? appUrl(cmd.annotationDownloadUrl) : undefined,
        curvatureNegativeThreshold: DEFAULT_SURFACE_CURVATURE_NEGATIVE_THRESHOLD,
        curvaturePositiveThreshold: DEFAULT_SURFACE_CURVATURE_POSITIVE_THRESHOLD,
      };
      return {
        ...surfaceLayer,
        surfaceColorMode: defaultSurfaceColorModeForLayer(surfaceLayer),
      };
    }

    if (layerType === 'segmentation') {
      return {
        ...base,
        type: 'segmentation',
        lut: (cmd.lut === 'binary' || cmd.lut === 'freesurfer') ? cmd.lut : undefined,
        customLutUrl: cmd.customLutDownloadUrl ? appUrl(cmd.customLutDownloadUrl) : undefined,
        brightness: 0,
        contrast: 1.0,
      };
    }

    return {
      ...base,
      type: 'intensity',
      brightness: 0,
      contrast: 1.0,
    };
  }, []);

  const handleLoadVolumeCommand = useCallback((cmd: LoadVolumeCommand) => {
    const layerType: LayerType = cmd.type === 'surface' || cmd.type === 'segmentation' || cmd.type === 'intensity'
      ? cmd.type
      : 'intensity';
    const persistCaseId = activeCaseId ?? initialCaseId ?? uploadCaseId;
    if (persistCaseId) {
      forgetClosedCaseVolume(persistCaseId, cmd.filename);
    }
    setVolumes(prev => {
      const existing = prev.find(v => v.filename === cmd.filename || v.id === cmd.filename);
      if (existing) {
        return prev.map(v => {
          if (v.filename === cmd.filename || v.id === cmd.filename) {
            return buildLoadedLayer(cmd, layerType);
          }
          return v;
        });
      }

      const newVolume = buildLoadedLayer(cmd, layerType);
      return [...prev, newVolume];
    });
  }, [activeCaseId, buildLoadedLayer, initialCaseId, setVolumes, uploadCaseId]);

  const handleCloseVolumeCommand = useCallback((cmd: { volume_id: string }) => {
    const persistCaseId = activeCaseId ?? initialCaseId ?? uploadCaseId;
    if (persistCaseId) {
      rememberClosedCaseVolume(persistCaseId, cmd.volume_id);
    }
    setVolumes(prev => prev.filter(v => v.filename !== cmd.volume_id && v.id !== cmd.volume_id));
  }, [activeCaseId, initialCaseId, setVolumes, uploadCaseId]);

  const handleSelectVolumesCommand = useCallback((cmd: { intensity_volume: string; segmentation_volume: string }) => {
    setVolumes(prev => prev.map(v => {
      const vType = v.type ?? 'intensity';
      if (vType === 'intensity') {
        if (!cmd.intensity_volume) return { ...v, visible: false };
        return (v.filename === cmd.intensity_volume || v.id === cmd.intensity_volume)
          ? { ...v, visible: true }
          : v;
      }
      if (vType === 'segmentation') {
        if (!cmd.segmentation_volume) return { ...v, visible: false };
        return (v.filename === cmd.segmentation_volume || v.id === cmd.segmentation_volume)
          ? { ...v, visible: true }
          : v;
      }
      return v;
    }));
  }, [setVolumes]);

  const handleAdjustDisplayCommand = useCallback((cmd: { opacity?: number; brightness?: number; contrast?: number }) => {
    setVolumes(prev => prev.map(v => {
      if (cmd.opacity !== undefined && v.type === 'segmentation') {
        return { ...v, opacity: cmd.opacity };
      }
      if (v.type === 'surface' || v.type === 'segmentation') {
        return v;
      }
      if (cmd.brightness !== undefined || cmd.contrast !== undefined) {
        return {
          ...v,
          brightness: cmd.brightness ?? v.brightness,
          contrast: cmd.contrast ?? v.contrast,
        };
      }
      return v;
    }));
  }, [setVolumes]);

  const updateVolume = useCallback((id: string, updates: Partial<Volume>) => {
    setVolumes(prev => prev.map(v => v.id === id ? { ...v, ...updates } : v));
  }, [setVolumes]);

  const removeVolume = useCallback((id: string) => {
    setVolumes(prev => prev.filter(v => v.id !== id));
  }, [setVolumes]);

  const reorderVolume = useCallback((sourceId: string, targetId: string, position: 'before' | 'after') => {
    setVolumes(prev => {
      if (sourceId === targetId) return prev;
      const source = prev.find(v => v.id === sourceId);
      const target = prev.find(v => v.id === targetId);
      if (!source || !target || layerTypeOf(source) !== layerTypeOf(target)) return prev;

      const layerType = layerTypeOf(source);
      const section = prev.filter(v => layerTypeOf(v) === layerType);
      const sourceIndex = section.findIndex(v => v.id === sourceId);
      const targetIndex = section.findIndex(v => v.id === targetId);
      if (sourceIndex < 0 || targetIndex < 0) return prev;

      const reorderedSection = [...section];
      const [moved] = reorderedSection.splice(sourceIndex, 1);
      const targetIndexAfterRemoval = reorderedSection.findIndex(v => v.id === targetId);
      const insertIndex = position === 'before' ? targetIndexAfterRemoval : targetIndexAfterRemoval + 1;
      reorderedSection.splice(insertIndex, 0, moved);

      let sectionIndex = 0;
      return prev.map(v => (
        layerTypeOf(v) === layerType
          ? reorderedSection[sectionIndex++]
          : v
      ));
    });
  }, [setVolumes]);

  const handleVolumeLutDetected = useCallback((volumeId: string, detectedLut: 'binary' | 'freesurfer' | undefined) => {
    setVolumes(prev => prev.map(v => {
      if (v.id !== volumeId) return v;
      if (v.type !== 'segmentation') return v;
      if (isMaskLikeVolume(v.filename)) return { ...v, lut: 'binary' as const };
      const lut: 'binary' | 'freesurfer' | undefined = detectedLut ?? v.lut;
      return { ...v, lut };
    }));
  }, [isMaskLikeVolume, setVolumes]);

  return {
    handleLoadVolumeCommand,
    handleCloseVolumeCommand,
    handleSelectVolumesCommand,
    handleAdjustDisplayCommand,
    updateVolume,
    removeVolume,
    reorderVolume,
    handleVolumeLutDetected,
  };
}
