/**
 * 校对页保存题目时的请求体构造。
 *
 * 单独抽出来是因为这里踩过一次坑：用手写白名单拼字段，漏掉了编程题的
 * language / test_cases / starter_code，后端把「没传」当作「清空」写回 null，
 * 教师配置的测试用例在一次保存后静默消失，编程题从此判不了分。
 *
 * 所以规则是：新增题目字段时，这里必须同步加；测试会盯着字段集合。
 */

/** 需要原样透传给后端的题目字段（不含 score，它要单独做 null 归一）。 */
export const QUESTION_PASSTHROUGH_FIELDS = [
  'question_type',
  'stem',
  'stem_images',
  'options',
  'answer',
  'analysis',
  'language',
  'test_cases',
  'starter_code',
  'difficulty',
  'tags',
  'source_chunk'
] as const

export interface QuestionSavePayload {
  question_type: string
  stem: string
  stem_images?: any
  options?: any
  answer?: any
  analysis?: any
  language?: string | null
  test_cases?: any
  starter_code?: string | null
  score: number | null
  difficulty?: any
  tags?: any
  source_chunk?: any
}

export function toQuestionSavePayload(questions: any[]): QuestionSavePayload[] {
  return (questions || []).map((q) => {
    const payload: Record<string, any> = {}
    for (const field of QUESTION_PASSTHROUGH_FIELDS) {
      payload[field] = q[field]
    }
    // undefined 与 null 都表示「未设置分值」，统一成 null 发给后端
    payload.score = q.score ?? null
    return payload as QuestionSavePayload
  })
}
