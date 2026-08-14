import { useCallback } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { LayerType, Volume } from '../types';
import { appUrl } from '../utils/api';
import { forgetClosedCaseVolume, rememberClosedCaseVolume } from '../utils/caseStorage';
import { createViewerLayer } from '../utils/layerBuilders';

function layerTypeOf(volume: Volume): LayerType {
  return volume.type;
}

interface UseWorkspaceVolumeStateArgs {
  activeCaseId: string | null;
  initialCaseId: string | null;
  uploadCaseId: string | null;
  setVolumes: Dispatch<SetStateAction<Volume[]>>;
}

interface LoadLayerCommand {
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

type LoadableLayerType = Exclude<LayerType, 'drawing'>;

export function useWorkspaceVolumeState({
  activeCaseId,
  initialCaseId,
  uploadCaseId,
  setVolumes,
}: UseWorkspaceVolumeStateArgs) {
  const buildLoadedLayer = useCallback((
    cmd: LoadLayerCommand,
    layerType: LoadableLayerType,
  ): Volume => {
    return createViewerLayer({
      filename: cmd.filename,
      url: `${appUrl(cmd.downloadPath)}?t=${Date.now()}`,
      type: layerType,
      lut: cmd.lut,
      customLutUrl: cmd.customLutDownloadUrl ? appUrl(cmd.customLutDownloadUrl) : undefined,
      curvatureUrl: cmd.curvatureDownloadUrl ? appUrl(cmd.curvatureDownloadUrl) : undefined,
      annotationUrl: cmd.annotationDownloadUrl ? appUrl(cmd.annotationDownloadUrl) : undefined,
      visible: cmd.visible ?? true,
    });
  }, []);

  const handleLoadLayerCommand = useCallback((cmd: LoadLayerCommand) => {
    const layerType: LoadableLayerType = cmd.type === 'surface' || cmd.type === 'segmentation' || cmd.type === 'intensity'
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
      return [newVolume, ...prev];
    });
  }, [activeCaseId, buildLoadedLayer, initialCaseId, setVolumes, uploadCaseId]);

  const handleRemoveLayersCommand = useCallback((layerIds: string[]) => {
    const persistCaseId = activeCaseId ?? initialCaseId ?? uploadCaseId;
    if (persistCaseId) {
      layerIds.forEach(layerId => rememberClosedCaseVolume(persistCaseId, layerId));
    }
    const removed = new Set(layerIds);
    setVolumes(prev => prev.filter(v => !removed.has(v.filename) && !removed.has(v.id)));
  }, [activeCaseId, initialCaseId, setVolumes, uploadCaseId]);

  const handleSetLayerVisibilityCommand = useCallback((changes: { layer_id: string; visible: boolean }[]) => {
    const visibilityById = new Map(changes.map(change => [change.layer_id, change.visible]));
    setVolumes(prev => prev.map(v => {
      const visible = visibilityById.get(v.id) ?? visibilityById.get(v.filename);
      return visible === undefined ? v : { ...v, visible };
    }));
  }, [setVolumes]);

  const handleSetLayerDisplayCommand = useCallback((
    layerIds: string[],
    updates: { opacity?: number; brightness?: number; contrast?: number; surface_color_mode?: 'solid' | 'curvature' | 'annotation' },
  ) => {
    const targets = new Set(layerIds);
    setVolumes(prev => prev.map(v => {
      if (!targets.has(v.id) && !targets.has(v.filename)) return v;
      const common = updates.opacity === undefined ? v : { ...v, opacity: updates.opacity };
      if (common.type === 'surface') {
        return {
          ...common,
          surfaceColorMode: updates.surface_color_mode ?? common.surfaceColorMode,
        };
      }
      return {
        ...common,
        brightness: updates.brightness ?? common.brightness,
        contrast: updates.contrast ?? common.contrast,
      };
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

  return {
    handleLoadLayerCommand,
    handleRemoveLayersCommand,
    handleSetLayerVisibilityCommand,
    handleSetLayerDisplayCommand,
    updateVolume,
    removeVolume,
    reorderVolume,
  };
}
