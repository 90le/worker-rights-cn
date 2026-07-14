# Intake Schema

Use this schema for structured labor rights case facts. Keep unknown values as `unknown` rather than guessing.

```yaml
case:
  jurisdiction:
    country: "China"
    city: ""
    main_work_location: ""
  parties:
    worker_name_or_alias: ""
    employer_legal_name: ""
    actual_managing_entity: ""
    dispatch_or_outsourcing: unknown
  employment:
    job_title: ""
    start_date: ""
    end_date_or_expected_end: ""
    current_status: "employed/notice_given/left/terminated/unknown"
    written_contract_signed: unknown
    contract_sign_date: ""
    contract_end_date: ""
    probation: unknown
  wage:
    average_monthly_wage: unknown
    last_12_months_wage_records: []
    unpaid_wages_amount: 0
    overtime_or_bonus_dispute: unknown
  social_security:
    social_insurance_paid: unknown
    housing_fund_paid: unknown
    payment_location: ""
  dispute:
    trigger: ""
    employer_stated_reason: ""
    worker_goal: ""
    documents_received: []
    documents_signed: []
    deadline_or_meeting_time: ""
  evidence:
    contract_or_offer: []
    wage_records: []
    attendance_records: []
    chat_or_email_records: []
    termination_or_agreement_docs: []
    social_insurance_records: []
    other: []
  risk_flags:
    pregnancy_or_maternity: false
    medical_period: false
    occupational_disease_or_work_injury: false
    non_compete: false
    executive_or_confidential_role: false
    foreign_worker: false
    group_layoff: false
```

## Completion Rules

- Prefer document-backed facts over memory.
- Mark contradictory facts explicitly.
- Preserve the original language used in notices or agreements for later review.
- Do not ask for identity numbers, bank card numbers, or unnecessary private data.
