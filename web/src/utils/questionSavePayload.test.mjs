import assert from 'node:assert/strict'
import { test } from 'node:test'

import { QUESTION_PASSTHROUGH_FIELDS, toQuestionSavePayload } from './questionSavePayload.ts'

test('编程题字段会随保存一起提交', () => {
  const [payload] = toQuestionSavePayload([
    {
      question_type: 'code',
      stem: '读入一个整数 n，输出 n 的两倍。',
      language: 'python',
      starter_code: 'n = int(input())\n',
      test_cases: [{ input: '21', expected_output: '42' }],
      answer: 'print(int(input()) * 2)',
      score: 8
    }
  ])

  // 这三个字段曾经被白名单漏掉，导致教师配好的用例被后端写回 null
  assert.equal(payload.language, 'python')
  assert.equal(payload.starter_code, 'n = int(input())\n')
  assert.deepEqual(payload.test_cases, [{ input: '21', expected_output: '42' }])
})

test('分值未设置时统一成 null 而不是 undefined', () => {
  const [payload] = toQuestionSavePayload([{ question_type: 'single', stem: 'x', score: undefined }])
  assert.equal(payload.score, null)
  assert.ok('score' in payload)
})

test('分值 0 是真实分值，不能被当成未设置', () => {
  const [payload] = toQuestionSavePayload([{ question_type: 'single', stem: 'x', score: 0 }])
  assert.equal(payload.score, 0)
})

test('新增题目字段时白名单必须同步，否则这里会失败', () => {
  const question = {
    question_type: 'code',
    stem: 'x',
    stem_images: [],
    options: null,
    answer: 'a',
    analysis: 'b',
    language: 'java',
    test_cases: [],
    starter_code: 'c',
    score: 1,
    difficulty: null,
    tags: [],
    source_chunk: null,
    // 故意多带一个字段：它不在白名单里，说明有人加了字段却没更新透传列表
    brand_new_field: 'should not leak'
  }

  const [payload] = toQuestionSavePayload([question])
  const missing = QUESTION_PASSTHROUGH_FIELDS.filter((f) => !(f in payload))
  assert.deepEqual(missing, [], `这些字段没被透传：${missing.join(', ')}`)
})

test('空列表安全', () => {
  assert.deepEqual(toQuestionSavePayload([]), [])
  assert.deepEqual(toQuestionSavePayload(undefined), [])
})
