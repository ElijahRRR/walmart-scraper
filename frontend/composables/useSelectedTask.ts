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
  // 可选字段：后端 SELECT * 实际返回，TaskList/ResultViewer 按需使用
  error_msg?: string | null
  updated_at?: string | null
  params?: Record<string, unknown> | null
}

export function useSelectedTask() {
  // 使用 useState 替代模块级 ref，避免 SSR 跨请求状态共享（各请求独立作用域）
  // SSR 侧永不写入（服务端无任务选择操作），客户端正常使用
  const selectedTask = useState<TaskItem | null>('selectedTask', () => null)

  function openTask(task: TaskItem) {
    selectedTask.value = task
  }

  function closeTask() {
    selectedTask.value = null
  }

  return {
    selectedTask: selectedTask as Readonly<typeof selectedTask>,
    openTask,
    closeTask,
  }
}
