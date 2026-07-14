# Legal Map For Layoff Defense

Retrieval date: 2026-06-16

This file is a routing aid for worker-side layoff and dismissal analysis in mainland China. It is not a complete legal database. Verify current law, local rules, and case facts before final advice.

## How To Use

- Cite legal bases as source-card anchors, for example `LCL-2012#art40` or `SPC-LDI-2-2025#art19`.
- Treat each mapping as a checklist: classification, evidence points, risk prompts, and source cards must travel together.
- Mark conclusions as `confirmed`, `possible`, or `lawyer_check`; do not convert an uncertain fact into a legal conclusion.
- Prefer current official sources. If a rule is local, missing, disputed, or only found in secondary sources, label it `local_verify` or `unverified`.
- Use `compensation-calculator` for amount estimation after the termination type is classified.

## Core Source Cards

```yaml
- id: "LCL-2012"
  title: "中华人民共和国劳动合同法"
  authority: "全国人民代表大会常务委员会"
  primary_url: "https://flk.npc.gov.cn/detail2.html?MmM5MDlmZGQ2NzhiZjE3OTAxNjc4YmY3NGQ3MTA2YjM%3D="
  official_text_url: "https://fgk.chinatax.gov.cn/zcfgk/c100009/c5193025/content.html"
  publish_date: "2012-12-28 amendment"
  effective_date: "2008-01-01; 2012 amendment effective 2013-07-01"
  jurisdiction: "national"
  source_type: "law"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Primary legal database plus official government text mirror used for article-level reading."

- id: "LCL-REG-2008"
  title: "中华人民共和国劳动合同法实施条例"
  authority: "国务院"
  url: "https://xzfg.moj.gov.cn/front/law/detail?LawID=284"
  publish_date: "2008-09-18"
  effective_date: "2008-09-18"
  jurisdiction: "national"
  source_type: "administrative_regulation"
  reliability: "official"
  retrieved_at: "2026-06-16"

- id: "PAID-LEAVE-REG-2007"
  title: "职工带薪年休假条例"
  authority: "国务院"
  url: "https://xzfg.moj.gov.cn/front/law/detail?LawID=208"
  publish_date: "2007-12-14"
  effective_date: "2008-01-01"
  jurisdiction: "national"
  source_type: "administrative_regulation"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Ministry of Justice national administrative-regulation database entry for State Council Order No. 514."

- id: "PAID-LEAVE-MEASURES-2008"
  title: "企业职工带薪年休假实施办法"
  authority: "人力资源和社会保障部"
  url: "https://www.moj.gov.cn/pub/sfbgw/flfggz/flfggzbmgz/200902/t20090209_144625.html"
  publish_date: "2008-09-18"
  effective_date: "2008-09-18"
  jurisdiction: "national"
  source_type: "department_rule"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Ministry of Justice official page for the ministry rule; replaces a State Council portal URL that returned 404 during the 2026 audit."

- id: "LDA-2007"
  title: "中华人民共和国劳动争议调解仲裁法"
  authority: "全国人民代表大会常务委员会"
  url: "https://gongbao.court.gov.cn/Details/997e66171cf55d219c613ec18dc370.html"
  verification_url: "https://www.gjxfj.gov.cn/gjxfj/xxgk/fgwj/flfg/webinfo/2016/03/1460585589964384.htm"
  publish_date: "2007-12-29"
  effective_date: "2008-05-01"
  jurisdiction: "national"
  source_type: "law"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Court Gazette URL may be slow; verification URL is an official government repost of the same law text."

- id: "SPC-LDI-1-2020"
  title: "最高人民法院关于审理劳动争议案件适用法律问题的解释（一）"
  authority: "最高人民法院"
  url: "https://www.court.gov.cn/fabu/xiangqing/282121.html"
  publish_date: "2020-12-29"
  effective_date: "2021-01-01"
  jurisdiction: "national"
  source_type: "judicial_interpretation"
  reliability: "official"
  retrieved_at: "2026-06-16"

- id: "SPC-LDI-2-2025"
  title: "最高人民法院关于审理劳动争议案件适用法律问题的解释（二）"
  authority: "最高人民法院"
  url: "https://www.court.gov.cn/fabu/xiangqing/472691.html"
  publish_date: "2025-08-01"
  effective_date: "2025-09-01"
  jurisdiction: "national"
  source_type: "judicial_interpretation"
  reliability: "official"
  retrieved_at: "2026-06-16"

- id: "PIPL-2021"
  title: "中华人民共和国个人信息保护法"
  authority: "全国人民代表大会常务委员会"
  url: "https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm"
  publish_date: "2021-08-20"
  effective_date: "2021-11-01"
  jurisdiction: "national"
  source_type: "law"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Cyberspace Administration page reposts China NPC text; used for personal-information minimization and anti-doxxing guardrails."

- id: "WRPL-2022"
  title: "中华人民共和国妇女权益保障法"
  authority: "全国人民代表大会常务委员会"
  url: "https://www.spp.gov.cn/spp/fl/202210/t20221030_591251.shtml"
  publish_date: "2022-10-30 amendment"
  effective_date: "2023-01-01"
  jurisdiction: "national"
  source_type: "law"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Supreme People's Procuratorate publication of the amended law text."

- id: "FEP-2012"
  title: "女职工劳动保护特别规定"
  authority: "国务院"
  url: "https://www.nhc.gov.cn/wjw/flfg/201204/0bdf1bf03a624f6ba02f7cb047fd750b.shtml"
  publish_date: "2012-04-28"
  effective_date: "2012-04-28"
  jurisdiction: "national"
  source_type: "administrative_regulation"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "National Health Commission page publishes State Council Order No. 619 text."

- id: "SIL-2018"
  title: "中华人民共和国社会保险法"
  authority: "全国人民代表大会常务委员会"
  url: "https://www.gjxfj.gov.cn/gjxfj/xxgk/fgwj/flfg/webinfo/2019/02/1536629579120511.htm"
  publish_date: "2018-12-29 amendment"
  effective_date: "2011-07-01; 2018 amendment effective 2018-12-29"
  jurisdiction: "national"
  source_type: "law"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Official government repost of the amended Social Insurance Law text."

- id: "CPL-2023"
  title: "中华人民共和国民事诉讼法"
  authority: "全国人民代表大会常务委员会"
  url: "https://gongbao.court.gov.cn/Details/886331ece0f6611a370642e89f08c6.html"
  verification_url: "https://ipc.court.gov.cn/zh-cn/news/view-3230.html"
  publish_date: "2023-09-01 amendment"
  effective_date: "2024-01-01 amendment effective"
  jurisdiction: "national"
  source_type: "law"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Court Gazette full-text entry plus Supreme People's Court IP Tribunal case page used for art114 and art118 verification."

- id: "SPC-EXTORTION-2013"
  title: "最高人民法院、最高人民检察院关于办理敲诈勒索刑事案件适用法律若干问题的解释"
  authority: "最高人民法院、最高人民检察院"
  url: "https://www.spp.gov.cn/zdgz/201304/t20130427_58482.shtml"
  publish_date: "2013-04-23"
  effective_date: "2013-04-27"
  jurisdiction: "national"
  source_type: "judicial_interpretation"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Supreme People's Procuratorate publication of the joint SPC/SPP interpretation."

- id: "SPC-IP-CRIM-2025"
  title: "最高人民法院、最高人民检察院关于办理侵犯知识产权刑事案件适用法律若干问题的解释"
  authority: "最高人民法院、最高人民检察院"
  url: "https://www.court.gov.cn/zixun/xiangqing/463291.html"
  publish_date: "2025-04-23"
  effective_date: "2025-04-26"
  jurisdiction: "national"
  source_type: "judicial_interpretation"
  reliability: "official"
  retrieved_at: "2026-06-16"
  notes: "Used for trade secret and unauthorized computer-system access guardrails."
```

## Article Anchor Index

### `LCL-2012`

- `art4`: employer rules and major policies must go through democratic procedure, employee discussion/consultation, and notice/publication before use as management basis.
- `art14`: open-ended labor contract conditions, including two consecutive fixed-term contracts and other statutory conditions.
- `art19`: probation period length limits.
- `art20`: probation wage floor.
- `art21`: probation dismissal must fit statutory conditions and employer must explain reasons.
- `art22`: service-period agreement and training-fee-based liquidated damages limits.
- `art23`: confidentiality and non-compete clauses; post-termination non-compete compensation and liquidated damages may be agreed for workers with confidentiality obligations.
- `art24`: non-compete personnel scope, business/geographic/time scope, competing employer or self-employment scope, and maximum two-year post-termination period.
- `art25`: employer may not require worker-paid liquidated damages except statutory service-period and non-compete circumstances.
- `art26`: labor contract clauses may be invalid if fraud, coercion, exploitation of distress, employer waiver of statutory duties, exclusion of worker rights, or illegality applies.
- `art30`: employer must pay labor remuneration in full and on time.
- `art35`: contract modification should be agreed by both parties and made in writing.
- `art36`: termination by mutual agreement.
- `art37`: worker resignation with 30 days' written notice, or 3 days during probation.
- `art38`: worker may terminate when employer has statutory violations such as unpaid wages, unpaid social insurance, unsafe conditions, invalid rules, or coercion.
- `art39`: employer fault dismissal grounds: probation nonconformity, serious rule violation, serious dereliction causing major harm, conflicting employment, invalid contract circumstances, or criminal liability.
- `art40`: non-fault dismissal with 30 days' notice or one month substitute wage for illness, incompetence after training/transfer, or major objective change after failed consultation.
- `art41`: economic layoff thresholds, statutory reasons, 30-day explanation/opinion procedure, labor authority report, priority retention groups, and rehire notice priority within six months.
- `art42`: protected-status bar against `art40` and `art41` termination.
- `art43`: employer unilateral termination must notify the trade union in advance and correct violations when the union raises opinions.
- `art44`: statutory labor contract termination events, including fixed-term expiry.
- `art45`: fixed-term expiry is extended until protected-status circumstances end when `art42` applies.
- `art46`: economic compensation triggers, including worker `art38` resignation, employer-proposed mutual termination, non-fault dismissal, economic layoff, and certain fixed-term expiries.
- `art47`: economic compensation calculation by years of service, monthly wage base, and high-wage cap.
- `art48`: unlawful termination remedy: continue performance if worker asks and feasible; otherwise pay statutory compensation.
- `art50`: employer must issue termination certificate and complete file/social insurance transfer procedures; worker must handle handover.
- `art82`: employer failure to sign a written labor contract after more than one month and less than one year can trigger double monthly wage exposure.
- `art85`: labor authority order and extra compensation risk for unpaid wages, low wage, overtime, or unpaid economic compensation.
- `art87`: unlawful termination compensation at twice the `art47` economic compensation standard.
- `art90`: worker may bear compensation liability if violating statutory resignation rules or confidentiality/non-compete duties causes employer loss.

### `LCL-REG-2008`

- `art6`: if employer fails to sign a written contract for more than one month but less than one year, it must pay double monthly wage under `LCL-2012#art82`.
- `art7`: if employer fails to sign a written contract for one full year, an open-ended contract is deemed formed and double monthly wage runs through the day before one full year.
- `art13`: employer and worker may not add contract termination conditions beyond statutory contract termination conditions.
- `art19`: lists statutory employer-side labor contract termination grounds under the Labor Contract Law.
- `art20`: substitute notice wage under `LCL-2012#art40` is calculated by the worker's previous month's wage.
- `art22`: task-based contract termination may trigger economic compensation under legal conditions.
- `art24`: termination certificate must state contract term, termination date, position, and years of service.
- `art25`: if employer pays unlawful termination compensation under `LCL-2012#art87`, it does not also pay economic compensation; compensation service years start from employment date.
- `art27`: monthly wage for economic compensation is the worker's due wage, including hourly/piece wage and monetary income such as bonus, allowance, and subsidy.

### `PAID-LEAVE-REG-2007`

- `art5`: if the employer cannot arrange annual leave due to work needs and the worker agrees not to take it, unused annual leave wage remuneration is paid at 300% of daily wage.

### `PAID-LEAVE-MEASURES-2008`

- `art10`: unused annual leave wage remuneration is paid at 300% of daily wage, including normal wage already paid during work.
- `art11`: daily wage for unused annual leave pay is converted from monthly wage by 21.75 monthly paid days.

### `LDA-2007`

- `art2`: labor dispute scope includes labor relationship confirmation, contract performance/change/termination, removal/dismissal/resignation, pay, social insurance, welfare, training, labor protection, injury medical fees, economic compensation, and damages.
- `art4`: parties may negotiate when a labor dispute arises; if negotiation is refused, fails, or a settlement agreement is not performed, the worker may apply for mediation.
- `art5`: one-arbitration-one-litigation path after failed negotiation/mediation.
- `art6`: evidence burden follows the claimant's assertion; employer controls some materials and may bear production responsibility.
- `art9`: labor authority complaint path for rights violations such as unpaid remuneration.
- `art21`: labor dispute arbitration commission jurisdiction.
- `art27`: one-year arbitration limitation period; labor remuneration during employment has special limitation rule.
- `art28`: arbitration application content requirements.
- `art29`: arbitration commission acceptance decision within five days; non-acceptance or overdue decision may lead to court filing.
- `art30`: after acceptance, arbitration application copy is sent to respondent; respondent may submit defense, but non-submission does not stop the proceeding.
- `art39`: evidence and cross-examination rules.
- `art43`: ordinary arbitration award time limit and extension.
- `art47`: certain labor disputes are final as to the employer side, including small-amount remuneration, injury medical fees, economic compensation, damages, and national standard disputes.
- `art48`: worker may sue after a final award if dissatisfied.
- `art50`: ordinary award litigation path.
- `art51`: effective mediation statement or award must be performed; if not performed, the other party may apply to court for enforcement.

### `SPC-LDI-1-2020`

- `art1`: court acceptance scope includes contract performance, termination, removal/dismissal/resignation, pay, social insurance-related disputes, and economic compensation.
- `art34`: if employment continues after contract expiry and no objection is raised, either party may end it but court supports economic compensation to the worker when due.
- `art35`: settlement agreement between employer and worker is valid if it does not violate mandatory law and has no fraud, coercion, or exploitation of distress; major misunderstanding or obvious unfairness may support revocation.
- `art36`: non-compete compensation defaults to 30% of the worker's 12-month average wage, with local minimum wage floor, when compensation was not agreed and the worker performed non-compete obligations.
- `art37`: where non-compete and compensation were agreed, performance and compensation are generally supported unless otherwise agreed at termination.
- `art38`: if employer fails to pay non-compete compensation for three months after termination due to employer reasons, worker can request解除 non-compete agreement.
- `art39`: employer may request解除 non-compete during the restriction period; worker may request three extra months of non-compete compensation when employer解除.
- `art40`: after worker pays liquidated damages for violating non-compete, employer may request continued performance of the non-compete agreement.
- `art44`: employer bears burden for decisions such as dismissal, removal, contract termination, pay reduction, service-year calculation, and rule-based action.
- `art45`: worker `LCL-2012#art38` resignation can support economic compensation when employer has statutory violations.
- `art46`: relation between union notice issue and unlawful termination litigation.
- `art47`: employer can supplement trade union notice procedure before litigation if it failed to notify before termination.
- `art50`: lawfully formulated rules and regulations may be used as adjudication basis if they have been publicized to the worker.

### `SPC-LDI-2-2025`

- `art8`: where a worker cannot enter a new contract during statutory protected circumstances, labor relationship may continue under original conditions until the circumstance disappears.
- `art10`: rules for fixed-term contract renewals and open-ended contract conditions.
- `art12`: service-period agreement with special treatment; worker early termination outside `LCL-2012#art38` may trigger actual-loss-based liability.
- `art13`: non-compete clause may be ineffective if worker did not know or contact trade secrets or IP-related confidential matters; unreasonable scope, geography, or term may be invalid to the excessive extent.
- `art14`: in-term non-compete clauses for senior managers, senior technical staff, and other confidentiality-obligated workers are not invalid merely because they are in-term or unpaid.
- `art15`: worker violating an effective non-compete agreement may need to return paid compensation and pay liquidated damages.
- `art16`: after unlawful termination, lists circumstances where a court may find continued labor contract performance impossible under `LCL-2012#art48`.
- `art17`: termination around occupational disease hazard exposure or required occupational health examination can trigger unlawful termination compensation.
- `art18`: if unlawful termination can continue to be performed, wages from unlawful termination decision to continued performance may be supported; mutual fault may be allocated.
- `art19`: worker resignation due to employer failure to pay social insurance contributions can support economic compensation.
- `art20`: arbitration limitation defense rules in litigation and retrial.

### `PIPL-2021`

- `art5`: personal information processing must follow legality, propriety, necessity, and good faith, and may not use misleading, fraudulent, or coercive methods.
- `art6`: personal information processing must have a clear and reasonable purpose, be directly related to that purpose, use the least-impact method, and avoid excessive collection.
- `art10`: organizations and individuals may not illegally collect, use, process, transmit, buy, sell, provide, or disclose another person's personal information.
- `art13`: personal information may be processed only under statutory bases such as consent, contract or lawful HR management necessity, legal duties, emergency protection, reasonable public-interest reporting, already-lawfully-disclosed information within reasonable scope, or other legal bases.
- `art28`: sensitive personal information includes data that may harm dignity or personal/property safety if leaked or misused, such as biometric, religious, specific identity, medical health, financial account, location tracking, and children's personal information.
- `art29`: processing sensitive personal information requires separate consent unless laws or administrative regulations provide otherwise.

### `WRPL-2022`

- `art48`: employer may not reduce a female worker's wages or benefits, restrict promotion or professional ranking, dismiss, or unilaterally terminate the labor contract or service agreement because of marriage, pregnancy, maternity leave, or nursing.

### `FEP-2012`

- `art5`: employer may not reduce a female worker's wage, dismiss her, or terminate her labor or employment contract because of pregnancy, childbirth, or nursing.
- `art6`: if a pregnant worker cannot adapt to the original work, the employer must reduce workload or arrange other suitable work based on medical institution proof.

### `SIL-2018`

- `art60`: employer must self-declare and pay social insurance contributions in full and on time, and may not postpone or reduce payment except for statutory reasons.

### `CPL-2023`

- `art114`: litigation participants or others who forge or destroy important evidence, obstruct case trial, or refuse to perform effective court judgments or rulings may be fined or detained depending on circumstances.
- `art118`: fine amount rules include a unit fine range of RMB 50,000 to RMB 1,000,000, relevant when an entity obstructs proceedings by destroying important evidence.

### `SPC-EXTORTION-2013`

- `art1`: extortion amounts of RMB 2,000-5,000, RMB 30,000-100,000, and RMB 300,000-500,000 are respectively treated as criminal-law thresholds for relatively large, huge, and especially huge amounts, with local standards set within the ranges.
- `art2`: certain circumstances may lower the relatively-large threshold by 50%, including prior extortion sanctions, extorting vulnerable persons, threatening serious violent crimes, using gang identity, impersonating special identities, or causing serious consequences.
- `art3`: extortion three or more times within two years may be treated as multiple extortion under criminal law.
- `art7`: knowingly providing communications, network, or similar assistance to another person's extortion crime may be treated as joint crime.

### `SPC-IP-CRIM-2025`

- `art16`: illegal copying to obtain trade secrets can be treated as theft, and unauthorized or over-authorized use of computer information systems to obtain trade secrets can be treated as electronic intrusion.
- `art17`: trade secret infringement reaches criminal seriousness when loss or illegal gains reach specified thresholds, with stricter treatment for repeat conduct and other serious circumstances.
- `art18`: trade secret loss amount can be determined by reasonable license fee, profit loss, infringer profit, trade secret value, or remediation costs depending on the conduct and consequence.
- `art21`: criminal proceedings involving trade secrets or confidential business information may use confidentiality measures, and breach of those measures or confidentiality duties can trigger liability.

## Termination Type Maps

### `mutual_termination`

Use when both sides negotiate or sign a separation agreement.

```yaml
classification:
  source_cards:
    - "LCL-2012#art36"
    - "LCL-2012#art46"
    - "SPC-LDI-1-2020#art35"
  claim_path:
    economic_compensation: "possible when employer proposed termination and parties agreed; verify proposal evidence and agreement text"
    unpaid_items: "always check wages, overtime, annual leave, bonus, social insurance, housing fund, and expense reimbursement separately"
    unlawful_termination_compensation: "possible if the agreement was coerced, fabricated, or used to cover an unlawful unilateral termination; set lawyer_check"
evidence_points:
  - signed or unsigned separation agreement versions
  - who first proposed separation and how it was communicated
  - HR chat/email/meeting notes about compensation, handover, leave date, waiver, and payment date
  - wage records for the 12 months before separation
  - unresolved claims excluded or released by the agreement
  - proof of pressure, deception, deadline threats, account lockout, or forced immediate handover
risk_prompts:
  - "Do not call it employer-proposed mutual termination unless proposal evidence exists."
  - "Broad wording such as all disputes settled can release later claims; mark lawyer_check before signing."
  - "If worker first proposes resignation or mutual termination, economic compensation may be weakened."
  - "Payment date, tax handling, social insurance month, non-compete, confidentiality, and reference/certificate wording need separate review."
  - "If signing already happened, review validity and revocation facts under settlement-agreement rules rather than assuming it is final."
```

### `employee_resignation`

Use when the worker submitted a resignation, but verify whether it was voluntary or statutory forced resignation.

```yaml
classification:
  source_cards:
    - "LCL-2012#art37"
    - "LCL-2012#art38"
    - "LCL-2012#art46"
    - "SPC-LDI-1-2020#art45"
    - "SPC-LDI-2-2025#art19"
  claim_path:
    no_compensation_resignation: "likely if the worker voluntarily resigns under art37 without employer statutory fault"
    economic_compensation: "possible if resignation clearly relies on art38 facts such as unpaid wages or unpaid social insurance"
    unpaid_items: "wages, overtime, annual leave, bonus, reimbursements, and statutory benefits remain separately reviewable"
evidence_points:
  - resignation letter text, submission time, recipient, and stated reason
  - chats showing resignation was demanded, induced, or tied to threatened dismissal
  - payroll, bank statements, payslips, tax records, attendance, and unpaid wage details
  - social insurance contribution records and gap months
  - proof of salary reduction, forced transfer, unsafe work, or rule violations
  - worker objection records before resignation
risk_prompts:
  - "Generic wording such as personal reasons usually weakens economic compensation; avoid retroactive rewriting."
  - "If not yet submitted, the reason should truthfully identify statutory employer violations when they exist."
  - "Leaving work without a valid legal basis can be reframed as absenteeism; preserve written notice and delivery proof."
  - "Social insurance underpayment, late payment, and non-payment can be treated differently by local practice; verify locally."
  - "Do not advise resignation until evidence can support the statutory reason and timing."
```

### `fault_dismissal`

Use when employer cites misconduct, serious rule violation, probation failure, dual employment, fraud, or criminal liability.

```yaml
classification:
  source_cards:
    - "LCL-2012#art4"
    - "LCL-2012#art21"
    - "LCL-2012#art39"
    - "LCL-2012#art43"
    - "LCL-2012#art48"
    - "LCL-2012#art87"
    - "SPC-LDI-1-2020#art44"
    - "SPC-LDI-1-2020#art50"
  claim_path:
    no_economic_compensation: "likely if statutory fault dismissal is valid"
    unlawful_termination_compensation: "possible if employer cannot prove statutory ground, rule validity, proportionality, or union procedure"
    reinstatement: "possible when worker wants continued performance and performance is feasible"
evidence_points:
  - written termination notice with exact reason and date
  - employee handbook/rules, democratic procedure records, publication or acknowledgment records
  - misconduct evidence, investigation records, warnings, loss calculation, witness records
  - probation recruitment conditions, assessment standards, and performance records
  - union notice and union opinion/correction records
  - prior comparable disciplinary cases to test consistency and proportionality
risk_prompts:
  - "Employer bears proof burden for dismissal decisions; ask for the written reason before arguing amount."
  - "Serious rule violation requires valid rules, worker notice, actual facts, and proportional treatment."
  - "Probation dismissal must connect to recruitment conditions and statutory grounds; vague performance dissatisfaction is risky for employer."
  - "Criminal liability is narrower than ordinary police warning, administrative detention, or internal suspicion."
  - "If employer paid no N because it used art39, the worker's core ask may be 2N or reinstatement, not N."
```

### `non_fault_dismissal`

Use when employer cites illness/medical limitation, incompetence after training or transfer, or major objective change.

```yaml
classification:
  source_cards:
    - "LCL-2012#art40"
    - "LCL-2012#art42"
    - "LCL-2012#art43"
    - "LCL-2012#art46"
    - "LCL-2012#art47"
    - "LCL-REG-2008#art20"
    - "LCL-REG-2008#art27"
    - "SPC-LDI-2-2025#art16"
    - "SPC-LDI-2-2025#art17"
  claim_path:
    economic_compensation: "normally triggered if art40 dismissal is valid"
    substitute_notice_wage: "possible if employer did not give 30 days' advance written notice"
    unlawful_termination_compensation: "possible if statutory reason, protected-status screening, training/transfer, consultation, or procedure is missing"
evidence_points:
  - written notice stating which art40 ground is used
  - 30-day notice proof or substitute notice wage calculation
  - medical-period records, job capability restrictions, and post-medical job arrangement
  - performance standards, poor-performance evidence, training records, transfer records, reassessment result
  - objective-change evidence, negotiation records, proposed modified contract terms, refusal reasons
  - pregnancy/maternity/nursing, medical treatment period, work injury, occupational disease exposure, or statutory protected status records
risk_prompts:
  - "Protected status under art42 blocks art40 dismissal; check this before calculating money."
  - "Incompetence usually requires evidence of training or position adjustment before dismissal."
  - "Major objective change is not the same as ordinary business preference or disguised cost reduction."
  - "N+1 is not a separate punishment; the +1 relates to missing 30-day notice."
  - "If termination is unlawful, compare reinstatement, 2N, and unpaid benefits during unlawful termination period."
```

### `economic_layoff`

Use when employer claims statutory mass layoff, reorganization, serious business difficulty, production shift, major technology innovation, business method adjustment, or major objective economic change.

```yaml
classification:
  source_cards:
    - "LCL-2012#art35"
    - "LCL-2012#art41"
    - "LCL-2012#art42"
    - "LCL-2012#art43"
    - "LCL-2012#art46"
    - "LCL-2012#art47"
    - "LCL-REG-2008#art19"
  claim_path:
    economic_compensation: "normally triggered if art41 layoff is valid"
    unlawful_termination_compensation: "possible if threshold, statutory reason, 30-day explanation/opinion process, labor authority report, priority retention, or protected-status screening is missing"
    rehire_priority: "track six-month rehire notice and priority if employer recruits again"
evidence_points:
  - total workforce count and number/percentage laid off
  - statutory layoff reason and supporting business documents
  - AI transformation, major technology innovation, production shift, or business-method adjustment evidence
  - proposed labor-contract change terms, internal alternative role analysis, and why layoffs remained necessary after consultation
  - 30-day explanation to trade union or all employees
  - employee opinion collection records and revised layoff plan
  - labor authority report receipt or filing proof
  - priority retention analysis: long fixed-term contract, open-ended contract, sole breadwinner, elderly/minor dependents
  - rehire postings and notices within six months after layoff
risk_prompts:
  - "If fewer than 20 workers and below 10% of workforce are affected, art41 may not apply; examine art40 or unlawful termination instead."
  - "Economic difficulty alone is insufficient without statutory process evidence."
  - "AI transformation can fit art41 only when the employer proves a real technology/business-method adjustment and the contract-change/continued-layoff chain."
  - "Art41 does not create a universal pre-layoff training duty, but no internal alternative, no contract-change consultation, or no priority-retention review can weaken the layoff path."
  - "If the employer frames the issue as worker incompetence or inability to adapt to an AI role, switch to art40 training or position-adjustment review."
  - "Open-ended labor contract status is an art41 priority-retention factor; it is not absolute immunity, but ignoring it is a material risk."
  - "Protected employees should be excluded or extended under statutory rules."
  - "Group layoff facts can affect leverage; preserve plan documents and group notices lawfully."
  - "Local labor authority filing practice may differ; verify city rules."
```

### `contract_expiry`

Use when employer lets a fixed-term contract expire, does not renew, or disputes continuation after expiry.

```yaml
classification:
  source_cards:
    - "LCL-2012#art14"
    - "LCL-2012#art44"
    - "LCL-2012#art45"
    - "LCL-2012#art46"
    - "LCL-2012#art47"
    - "LCL-REG-2008#art13"
    - "SPC-LDI-1-2020#art34"
    - "SPC-LDI-2-2025#art8"
    - "SPC-LDI-2-2025#art10"
  claim_path:
    economic_compensation: "possible when employer does not renew, except where employer maintains or improves terms and worker refuses renewal"
    open_ended_contract: "possible if statutory open-ended conditions are met"
    unlawful_termination_compensation: "possible if employer treats expiry as termination despite protected status or continued employment facts"
evidence_points:
  - fixed-term contract copies and renewal history
  - renewal offer terms, employee response, and delivery records
  - post-expiry work records, attendance, payroll, system access, task assignment
  - proof of two consecutive fixed-term contracts or other open-ended contract triggers
  - protected status at expiry: pregnancy/maternity/nursing, medical period, work injury, occupational disease exposure
  - termination certificate wording and date
risk_prompts:
  - "Do not assume contract expiry means zero compensation; check art46 renewal exception."
  - "Protected status can extend the contract until statutory circumstance disappears."
  - "Continued work after expiry may change the analysis."
  - "Open-ended contract conditions can convert the dispute from expiry compensation to unlawful termination or contract continuation."
  - "Employer cannot create extra termination conditions beyond statutory termination conditions."
```

### `constructive_dismissal`

Use when the worker is pressured to leave because of unpaid wages, unpaid social insurance, unilateral salary reduction, forced transfer, unsafe conditions, account lockout, harassment, or other employer breach.

```yaml
classification:
  source_cards:
    - "LCL-2012#art30"
    - "LCL-2012#art35"
    - "LCL-2012#art38"
    - "LCL-2012#art46"
    - "LCL-2012#art85"
    - "LDA-2007#art9"
    - "SIL-2018#art60"
    - "SPC-LDI-1-2020#art45"
    - "SPC-LDI-2-2025#art19"
  claim_path:
    economic_compensation: "possible when worker terminates based on proven art38 employer violations"
    unpaid_wages_or_benefits: "pursue separately with payroll, attendance, wage standard, and payment records"
    unlawful_dismissal: "possible if employer actually locked out, removed, or dismissed the worker while disguising it as resignation"
evidence_points:
  - salary reduction notice, transfer notice, changed job duties, changed workplace, and worker objection
  - payroll/bank/tax/social-insurance records showing arrears or contribution gaps
  - work account lockout, access removal, forced leave, attendance abnormality records
  - HR messages requiring resignation or threatening termination
  - written worker notice that truthfully cites statutory employer breach before leaving
  - complaint records to labor authority if used
risk_prompts:
  - "The resignation reason and evidence must match; do not advise adding false statutory reasons."
  - "Unilateral salary reduction or transfer needs fact-specific review of contract terms, reasonableness, and written objections."
  - "Account lockout may support employer-side termination facts, but preserve timing and screenshots lawfully."
  - "Social insurance disputes may split between administrative enforcement and labor dispute compensation; verify local path."
  - "If the worker is still employed, prioritize written objection, evidence preservation, and lawful attendance strategy."
```

### `unclear_or_mixed`

Use when documents conflict, the employer gives no written reason, or the facts contain both resignation and dismissal signals.

```yaml
classification:
  source_cards:
    - "LCL-2012#art43"
    - "LCL-2012#art48"
    - "LCL-2012#art87"
    - "LCL-2012#art50"
    - "LCL-2012#art82"
    - "LCL-REG-2008#art6"
    - "LCL-REG-2008#art7"
    - "LDA-2007#art6"
    - "LDA-2007#art27"
    - "SPC-LDI-1-2020#art44"
  claim_path:
    preserve_status: "avoid signing new characterization before classification"
    request_written_reason: "ask employer to identify termination basis, date, and settlement amount"
    limitation_check: "track arbitration limitation and wage-related special rule"
evidence_points:
  - all notices, agreements, resignation forms, termination certificates, and HR chats
  - timeline of access removal, handover, last work day, last pay day, and social insurance stop month
  - witness names, meeting notes, and lawful recordings where local law and evidence rules allow
  - proof of worker's continued willingness to work if seeking reinstatement
  - employer-controlled documents to request or ask tribunal to order production
risk_prompts:
  - "Do not let employer's label control the classification; facts and legal basis matter."
  - "If user wants maximum benefit, frame it as lawful maximum under evidence and source-card mapping."
  - "If documents contain broad waiver or resignation language, switch to agreement review before money calculation."
  - "If the claim may be near limitation period, escalate to lawyer_check and arbitration filing timeline."
```

## Protected-Status Gate

Run this gate before final classification for `non_fault_dismissal`, `economic_layoff`, and `contract_expiry`.

```yaml
source_cards:
  - "LCL-2012#art42"
  - "LCL-2012#art45"
  - "WRPL-2022#art48"
  - "FEP-2012#art5"
  - "FEP-2012#art6"
  - "SPC-LDI-2-2025#art8"
  - "SPC-LDI-2-2025#art17"
check:
  - occupational disease exposure without required pre-departure health examination
  - suspected or confirmed occupational disease diagnosis/observation period
  - work injury or occupational disease with lost or partially lost labor capacity
  - statutory medical treatment period
  - pregnancy, maternity, or nursing period
  - continuous service at employer for 15 years and less than 5 years from statutory retirement age
  - other statutory protected circumstances
risk_prompts:
  - "If protected status applies, do not treat art40 or art41 termination as valid without lawyer_check."
  - "Fixed-term expiry may be extended until the protected circumstance ends."
  - "Occupational health examination issues can independently support unlawful termination risk."
```

## Evidence Bundle By Output Field

Use these bundles when `layoff-defense` produces `evidence_gaps`.

```yaml
identity_and_relationship:
  source_cards:
    - "LDA-2007#art2"
    - "LDA-2007#art6"
  documents:
    - labor contract and renewals
    - offer letter and onboarding records
    - social insurance and tax records
    - attendance, payroll, work chats, email, badge/system records

termination_characterization:
  source_cards:
    - "LCL-2012#art36"
    - "LCL-2012#art37"
    - "LCL-2012#art39"
    - "LCL-2012#art40"
    - "LCL-2012#art41"
    - "LCL-2012#art44"
    - "SPC-LDI-1-2020#art44"
  documents:
    - termination notice
    - resignation letter
    - separation agreement
    - termination certificate
    - HR communication timeline
    - account lockout and handover records

money_basis:
  source_cards:
    - "LCL-2012#art46"
    - "LCL-2012#art47"
    - "LCL-REG-2008#art20"
    - "LCL-REG-2008#art25"
    - "LCL-REG-2008#art27"
    - "PAID-LEAVE-REG-2007#art5"
    - "PAID-LEAVE-MEASURES-2008#art10"
    - "PAID-LEAVE-MEASURES-2008#art11"
  documents:
    - 12-month wage records before termination
    - previous month's wage record when claiming substitute notice wage
    - bonus, allowance, subsidy, commission, overtime, and deduction details
    - years of service proof
    - local average wage and cap source, if high-wage cap is triggered

procedure_and_burden:
  source_cards:
    - "LCL-2012#art4"
    - "LCL-2012#art43"
    - "LDA-2007#art6"
    - "SPC-LDI-1-2020#art44"
    - "SPC-LDI-1-2020#art50"
  documents:
    - handbook/rules democratic procedure and publication proof
    - union notice and opinion records
    - employer investigation and decision records
    - documents controlled by employer that should be requested
```

## Verification Gaps Still Required

- Local wage cap and average wage data for the case city and termination year.
- Local wage payment rules, medical-period rules, sick-pay rules, annual-leave practice, and arbitration practice.
- Social insurance and housing fund enforcement path by city.
- Whether local arbitration/court treats social insurance underpayment, late payment, or non-payment as sufficient for `LCL-2012#art38`.
- Sector-specific or identity-specific issues: labor dispatch, outsourcing, platform work, executive status, foreign worker, non-compete, stock options, confidentiality, and public-sector employer.
