import { ref } from 'vue'
import { api } from '../../api.js'

// Progress tab state: tool results opened by the resource query and the
// workspace artifact directory. The page decides when to load or reset them.
export function useJobProgress({job, jobId, route, scalar, sameJob}) {
  const artifactDownloadUrl=artifact=>`/api/cockpit/jobs/${encodeURIComponent(job.value.id)}/workspace-artifact/download?` + new URLSearchParams({
    scene_id: job.value.scene_id,
    path: artifact.path,
    ...(workspaceArtifacts.value?.execution_id?{execution_id:workspaceArtifacts.value.execution_id}:{})
  })
  const resource = ref(null),
    resourceText = ref(''),
    resourceLoading = ref(false),
    resourceError = ref('')
  const workspaceArtifacts = ref(null),
    workspaceArtifactsLoading = ref(false),
    workspaceArtifactsError = ref('')
  const workspaceFile = ref(null), workspaceFileLoading = ref(false), workspaceFileError = ref('')
  const workspaceFileTarget = ref(null)
  let resourceRequest = 0,
    artifactsRequest = 0,
    fileRequest = 0
  function resetWorkspaceFile() {
    ++fileRequest;
    workspaceFile.value = null;
    workspaceFileTarget.value = null;
    workspaceFileLoading.value = false;
    workspaceFileError.value = ''
  }
  // The progress tab's part of the page's reset of state tied to one job version.
  function resetProgress() {
    ++resourceRequest;
    ++artifactsRequest;
    resource.value = null;
    resourceText.value = '';
    resourceLoading.value = false;
    resourceError.value = ''
    workspaceArtifacts.value = null;
    workspaceArtifactsLoading.value = false;
    workspaceArtifactsError.value = '';
    resetWorkspaceFile()
  }
  function dropResource() {
    ++resourceRequest;
    resource.value = null;
    resourceText.value = ''
  }
  async function loadWorkspaceArtifacts() {
    if (!job.value) return
    const current = job.value, request = ++artifactsRequest
    workspaceArtifactsLoading.value = true;
    workspaceArtifactsError.value = ''
    try {
      const value = await api(`/api/cockpit/jobs/${encodeURIComponent(current.id)}/workspace-artifacts?scene_id=${encodeURIComponent(current.scene_id)}`)
      if (request !== artifactsRequest || !sameJob(current)) return
      if (workspaceArtifacts.value?.execution_id !== value.execution_id) resetWorkspaceFile()
      workspaceArtifacts.value = value
    } catch (error) {
      if (request !== artifactsRequest || !sameJob(current)) return
      workspaceArtifacts.value = null;
      resetWorkspaceFile()
      if (error.status !== 404) workspaceArtifactsError.value = error.message
    } finally {
      if (request === artifactsRequest) workspaceArtifactsLoading.value = false
    }
  }
  async function loadWorkspaceArtifact(path, offset = 0) {
    if (!job.value || !workspaceArtifacts.value) return
    const current = job.value
    const executionId = workspaceArtifacts.value.execution_id
    const previous = workspaceFile.value
    if (offset && (workspaceFileTarget.value?.path !== path || workspaceFileTarget.value?.executionId !== executionId || previous?.next_offset !== offset)) return
    const request = ++fileRequest
    if (!offset) workspaceFile.value = null
    workspaceFileTarget.value = { path, executionId }
    workspaceFileLoading.value = true;
    workspaceFileError.value = ''
    try {
      const value = await api(`/api/cockpit/jobs/${encodeURIComponent(current.id)}/workspace-artifact?` + new URLSearchParams({
        scene_id: current.scene_id,
        path,
        offset,
        ...(executionId?{execution_id:executionId}:{})
      }))
      if (request !== fileRequest || !sameJob(current)) return
      if (value.execution_id !== executionId) {
        workspaceFileError.value = '产物快照已变化，本次内容未合并。请刷新工作目录后重新选择文件。'
        return
      }
      workspaceFile.value = offset && executionId ? { ...value, content: previous.content + value.content } : value
    } catch (error) {
      if (request === fileRequest && sameJob(current)) workspaceFileError.value = error.message
    }
    finally {
      if (request === fileRequest) workspaceFileLoading.value = false
    }
  }
  async function loadResource(id, offset = 0) {
    if (!job.value) return
    const current = job.value, request = ++resourceRequest
    resourceLoading.value = true;
    resourceError.value = ''
    if (!offset) {
      resource.value = null;
      resourceText.value = ''
    }
    if (!current.result_ids.includes(id)) {
      resourceError.value = '该资料没有关联到当前工作。';
      resourceLoading.value = false;
      return
    }
    try {
      const value = await api(`/api/cockpit/tool-results/${encodeURIComponent(id)}?` + new URLSearchParams({ scene_id: current.scene_id, offset }))
      if (request !== resourceRequest || current.id !== jobId.value || scalar(route.query.resource) !== id) return
      resource.value = value;
      resourceText.value = offset ? resourceText.value + value.content : value.content
    } catch (error) {
      if (request === resourceRequest) resourceError.value = error.message
    }
    finally {
      if (request === resourceRequest) resourceLoading.value = false
    }
  }
  return {
    resource, resourceText, resourceLoading, resourceError, workspaceArtifacts,
    workspaceArtifactsLoading, workspaceArtifactsError, workspaceFile, workspaceFileLoading,
    workspaceFileError, workspaceFileTarget, artifactDownloadUrl, resetWorkspaceFile,
    loadWorkspaceArtifacts, loadWorkspaceArtifact, loadResource, resetProgress, dropResource
  }
}
