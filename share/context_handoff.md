# Context Handoff (Dense, LLM-Readable)

## Exact current technical goal
Stabilize newly added **addon modules** (escalation + email queue + analytics) so role workflows match expected behavior: quarter-scoped reporting, non-reappearing resolved escalations on re-scan, correct open-quarter handling in completion/analytics, manager comment validation/display, and clear shared-goal weightage edit UX.

## Code changes made so far

### 1. Addon foundation (already implemented earlier in this session)
- **Data models**
  - `app/models.py`: added `Escalation` and `Notification`.
- **Services**
  - `app/services/escalation_service.py` (new): quarter scan for missed employee check-ins and missed manager reviews; escalation creation + email queueing.
  - `app/services/notification_service.py` (new): queue email records and SMTP dispatch for queued notifications.
- **Admin routes/views**
  - `app/routes/admin.py`: added `/admin/analytics`, `/admin/escalations`, `/admin/notifications`, escalation scan/resolve handlers, queued email send handler, dashboard counters.
  - `app/templates/admin/analytics.html` (new), `app/templates/admin/escalations.html` (new), `app/templates/admin/notifications.html` (new).
  - `app/templates/admin/dashboard.html`: quick links + escalation/email counters.
- **Manager routes/views**
  - `app/routes/manager.py`: added `/manager/escalations`.
  - `app/templates/manager/escalations.html` (new).
  - `app/templates/manager/dashboard.html`: escalation counter/link.

### 2. In-progress bugfix pass for latest user-reported issues (current working set)
- **Escalation re-scan behavior fixed**
  - `app/services/escalation_service.py`:
    - Added global dedupe lookup (`_find_escalation`) so resolved records are not recreated on subsequent scans.
    - Added auto-resolution of open escalation when underlying condition is now satisfied:
      - employee check-in now present -> resolve `employee_missed_checkin`
      - manager comment now present -> resolve `manager_missed_review`
    - Scan result now returns created + auto-resolved counts.
- **Quarter gating/reporting corrections**
  - `app/routes/admin.py`:
    - Added `get_reportable_quarters()` combining open windows + quarters with real Achievement/CheckIn data.
    - `/admin/completion` now marks unopened/no-data quarter as unavailable (`quarter_available=False`) and reports zeroed rows (not false “locked progress”).
    - `/admin/analytics` quarter table now flags non-reportable quarters and shows “Not Open”.
    - Escalation scan success message now includes auto-resolve counts.
- **Audit visibility improvements**
  - `app/routes/admin.py` `/audit`: passes users map to template.
  - `app/templates/admin/audit.html`: shows user names for `changed_by`.
  - `app/routes/admin.py` `/escalations/resolve/{id}`: writes audit entry (`escalation_status: open -> resolved`) when goal-linked escalation is resolved.
- **Manager check-in fixes**
  - `app/routes/manager.py`:
    - `comment` and `quarter` form params now safe defaults.
    - explicit validation for blank comment -> redirect with error (prevents bad flow).
    - passes `manager_comments` to template for visibility.
  - `app/templates/manager/checkin.html`:
    - comment field set `required`.
    - added “Saved Manager Comments” table per goal.
- **Employee visibility / shared weightage UX**
  - `app/routes/employee.py`: passes per-goal manager comments to check-in template.
  - `app/templates/employee/checkin.html`: shows manager comments history.
  - `app/templates/employee/goals.html`: for shared-recipient draft goals outside goal-setting window, explicitly shows non-editable message (“Editable in May–June”) so behavior is clear.
- **Admin UI clarity**
  - `app/templates/admin/completion.html`: adds “Not Open” state message/badge.
  - `app/templates/admin/analytics.html`: displays reportable quarter list and “Not Open” row state.
  - `app/templates/admin/escalations.html`: added quarter column.

## Current error/state
- **Code state**: in-progress uncommitted changes; compile pass currently succeeds (`./venv/bin/python -m compileall app`).
- **Dirty files now**:
  - Modified:  
    `app/models.py`, `app/routes/admin.py`, `app/routes/employee.py`, `app/routes/manager.py`,  
    `app/templates/admin/audit.html`, `app/templates/admin/completion.html`, `app/templates/admin/dashboard.html`,  
    `app/templates/employee/checkin.html`, `app/templates/employee/goals.html`,  
    `app/templates/manager/checkin.html`, `app/templates/manager/dashboard.html`,  
    `atomquest.db`
  - New:  
    `app/services/escalation_service.py`, `app/services/notification_service.py`,  
    `app/templates/admin/analytics.html`, `app/templates/admin/escalations.html`, `app/templates/admin/notifications.html`,  
    `app/templates/manager/escalations.html`
- **Environment constraint**: absolute path `/share/context_handoff.md` is not writable in this environment (`permission denied creating /share`).  
  This handoff is saved at: `/home/ak/atomquest-portal/share/context_handoff.md`.

## Precise next step
Run targeted role-flow regression after this bugfix pass and only then finalize:
1. Admin: completion + analytics for Q2/Q3/Q4 should show “Not Open/0” unless quarter has data/open override.
2. Admin escalation: resolve item, re-run scan for same quarter; resolved item must not be recreated unless a new unique condition appears.
3. Manager: save-comment with empty comment must show validation error (no broken route behavior), and saved comments must render.
4. Employee: shared recipient weightage edit must appear only in goal-setting window; outside window should show explicit non-editable reason.
