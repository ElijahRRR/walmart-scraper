/**
 * useSelectedTask — 跨组件共享「当前查看结果的任务」状态。
 *
 * TaskList 点击「查看结果」时调用 openTask(task)，
 * ResultViewer 监听 selectedTask 并显示对应结果。
 *
 * 用法：
 *   const { selectedTask, openTask, closeTask } = useSelectedTask()
 */

export interface TaskItem {
  id: number
  type: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'blocked'
  progress: number
  total: number
  result_count: number
  created_at: string
}

// 模块级单例，确保所有组件共享同一状态
const _selectedTask = ref<TaskItem | null>(null)

export function useSelectedTask() {
  function openTask(task: TaskItem) {
    _selectedTask.value = task
  }

  function closeTask() {
    _selectedTask.value = null
  }

  return {
    selectedTask: _selectedTask as Readonly<typeof _selectedTask>,
    openTask,
    closeTask,
  }
}
