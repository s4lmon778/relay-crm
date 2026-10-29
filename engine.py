"""Relay CRM: transactional customer records and agreement-aware pipeline."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
import csv
import hashlib
import io
import json
import sqlite3

STAGES = {'Lead': 10, 'Qualified': 40, 'Proposal': 70, 'Won': 100, 'Lost': 0}
TRANSITIONS = {'Lead': {'Qualified', 'Lost'}, 'Qualified': {'Lead', 'Proposal', 'Lost'},
               'Proposal': {'Qualified', 'Won', 'Lost'}, 'Won': set(), 'Lost': {'Lead'}}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounts(
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS contacts(
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
 name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS opportunities(
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
 contact_id INTEGER NOT NULL REFERENCES contacts(id), title TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),
 stage TEXT NOT NULL CHECK(stage IN ('Lead','Qualified','Proposal','Won','Lost')),
 next_action TEXT NOT NULL, due_date TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS opportunities_stage ON opportunities(stage, due_date);
CREATE INDEX IF NOT EXISTS contacts_account ON contacts(account_id);
CREATE TABLE IF NOT EXISTS agreements(
 id INTEGER PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
 opportunity_version INTEGER NOT NULL, snapshot TEXT NOT NULL, digest TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('draft','completed')) DEFAULT 'draft');
CREATE TABLE IF NOT EXISTS activities(
 id INTEGER PRIMARY KEY, opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
 kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests(
 key TEXT PRIMARY KEY, digest TEXT NOT NULL, response TEXT NOT NULL);
'''

class Problem(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status

def text(data, key, limit=180):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise Problem(f'{key} must contain 1-{limit} characters')
    return value.strip()

def integer(data, key, minimum=0):
    value = data.get(key)
    if type(value) is not int or not minimum <= value <= 9007199254740991:
        raise Problem(f'{key} must be a safe integer of at least {minimum}')
    return value

def due(data):
    value = text(data, 'due_date', 10)
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
    except ValueError:
        raise Problem('due_date must be YYYY-MM-DD')
    return value

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

class Engine:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA journal_mode=WAL')
        try:
            yield con
        finally:
            con.close()

    def activity(self, con, ident, kind, detail):
        con.execute('INSERT INTO activities(opportunity_id,kind,detail,created_at) VALUES(?,?,?,?)',
                    (ident, kind, detail, datetime.now(timezone.utc).isoformat()))

    def one(self, con, table, ident):
        if table not in {'accounts', 'contacts', 'opportunities', 'agreements'}:
            raise ValueError('Unknown table')
        row = con.execute(f'SELECT * FROM {table} WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise Problem('Record not found', 404)
        return dict(row)

    def mutate(self, action, data, key):
        if not isinstance(data, dict):
            raise Problem('A JSON object is required')
        if not isinstance(key, str) or not 1 <= len(key) <= 160:
            raise Problem('An Idempotency-Key of 1-160 characters is required')
        digest = hashlib.sha256(canonical([action, data]).encode()).hexdigest()
        with self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            try:
                prior = con.execute('SELECT * FROM requests WHERE key=?', (key,)).fetchone()
                if prior:
                    if prior['digest'] != digest:
                        raise Problem('This request key was already used for different content', 409)
                    result = json.loads(prior['response'])
                else:
                    result = self._apply(con, action, data)
                    con.execute('INSERT INTO requests VALUES(?,?,?)', (key, digest, canonical(result)))
                con.execute('COMMIT')
                return result
            except sqlite3.IntegrityError as exc:
                con.execute('ROLLBACK')
                raise Problem('A unique customer record already exists or a related record is invalid', 409) from exc
            except Exception:
                con.execute('ROLLBACK')
                raise

    def _apply(self, con, action, data):
        if action == 'accounts':
            domain = text(data, 'domain').lower()
            if any(c.isspace() for c in domain) or '.' not in domain or '://' in domain:
                raise Problem('Use a domain such as example.test')
            ident = con.execute('INSERT INTO accounts(name,domain) VALUES(?,?)',
                                (text(data, 'name'), domain)).lastrowid
            return self.one(con, 'accounts', ident)
        if action == 'contacts':
            account = integer(data, 'account_id', 1)
            self.one(con, 'accounts', account)
            email = text(data, 'email').lower()
            if email.count('@') != 1 or any(c.isspace() for c in email) or '.' not in email.split('@')[1]:
                raise Problem('Use a complete email address')
            ident = con.execute('INSERT INTO contacts(account_id,name,email) VALUES(?,?,?)',
                                (account, text(data, 'name'), email)).lastrowid
            return self.one(con, 'contacts', ident)
        if action == 'opportunities':
            account, contact = integer(data, 'account_id', 1), integer(data, 'contact_id', 1)
            self.one(con, 'accounts', account)
            if self.one(con, 'contacts', contact)['account_id'] != account:
                raise Problem('The primary contact must belong to this account')
            ident = con.execute('''INSERT INTO opportunities
              (account_id,contact_id,title,amount_cents,stage,next_action,due_date)
              VALUES(?,?,?,?,'Lead',?,?)''', (account, contact, text(data, 'title'),
                integer(data, 'amount_cents'), text(data, 'next_action'), due(data))).lastrowid
            self.activity(con, ident, 'created', 'Opportunity opened in Lead')
            return self.one(con, 'opportunities', ident)
        if action not in {'update', 'note', 'issue', 'complete'}:
            raise Problem('Unknown operation', 404)
        op = self.one(con, 'opportunities', integer(data, 'id', 1))
        if action == 'note':
            self.activity(con, op['id'], 'note', text(data, 'detail', 2000))
            return {'saved': True}
        if integer(data, 'version', 1) != op['version']:
            raise Problem('This opportunity changed. Reload its current version before saving.', 409)
        if action == 'issue':
            if op['stage'] != 'Proposal':
                raise Problem('Move the opportunity to Proposal before preparing an agreement')
            snapshot = canonical({k: op[k] for k in ('id', 'account_id', 'contact_id', 'title', 'amount_cents', 'version')})
            agreement = con.execute('INSERT INTO agreements(opportunity_id,opportunity_version,snapshot,digest) VALUES(?,?,?,?)',
                (op['id'], op['version'], snapshot, hashlib.sha256(snapshot.encode()).hexdigest())).lastrowid
            self.activity(con, op['id'], 'agreement', f'Agreement {agreement} prepared for version {op["version"]}')
            return self.one(con, 'agreements', agreement)
        if action == 'complete':
            agreement = self.one(con, 'agreements', integer(data, 'agreement_id', 1))
            if agreement['opportunity_id'] != op['id'] or agreement['opportunity_version'] != op['version']:
                raise Problem('Agreement does not match the current opportunity version', 409)
            if op['stage'] != 'Proposal' or data.get('digest') != agreement['digest']:
                raise Problem('Agreement reference or stage is invalid', 409)
            if agreement['status'] != 'completed':
                con.execute("UPDATE agreements SET status='completed' WHERE id=?", (agreement['id'],))
                self.activity(con, op['id'], 'agreement', f'Demo completion recorded for agreement {agreement["id"]}')
            return self.one(con, 'agreements', agreement['id'])
        stage = data.get('stage', op['stage'])
        if not isinstance(stage,str) or stage not in STAGES:
            raise Problem('Unknown pipeline stage')
        if stage != op['stage'] and stage not in TRANSITIONS[op['stage']]:
            raise Problem(f'{op["stage"]} cannot transition directly to {stage}')
        if op['stage'] in {'Won', 'Lost'} and stage == op['stage']:
            raise Problem('Reopen a lost opportunity before changing its details; won deals are closed')
        amount = integer(data, 'amount_cents') if 'amount_cents' in data else op['amount_cents']
        if stage == 'Won':
            if amount != op['amount_cents']:
                raise Problem('Save new commercial terms and prepare a fresh agreement before closing', 409)
            accepted = con.execute("SELECT id FROM agreements WHERE opportunity_id=? AND opportunity_version=? AND status='completed'", (op['id'], op['version'])).fetchone()
            if not accepted:
                raise Problem('A completed agreement matching these terms is required to close Won', 409)
        next_action = text(data, 'next_action') if 'next_action' in data else op['next_action']
        due_date = due(data) if 'due_date' in data else op['due_date']
        con.execute('UPDATE opportunities SET stage=?,amount_cents=?,next_action=?,due_date=?,version=version+1 WHERE id=?',
                    (stage, amount, next_action, due_date, op['id']))
        self.activity(con, op['id'], 'updated', f'{op["stage"]} → {stage}; value {amount} cents; version {op["version"]+1}')
        return self.one(con, 'opportunities', op['id'])

    def workspace(self):
        with self.connection() as con:
            con.execute('BEGIN')
            return {name: [dict(r) for r in con.execute('SELECT * FROM '+name+' ORDER BY id')]
                    for name in ('accounts', 'contacts', 'opportunities', 'agreements', 'activities')}

    def report(self, today=None):
        today = today or date.today().isoformat()
        with self.connection() as con:
            con.execute('BEGIN')
            rows = [dict(r) for r in con.execute('''SELECT stage,COUNT(*) AS count,
                SUM(amount_cents) AS value_cents FROM opportunities GROUP BY stage''')]
            overdue = con.execute("SELECT COUNT(*) FROM opportunities WHERE stage NOT IN ('Won','Lost') AND due_date<?", (today,)).fetchone()[0]
            accounts = [dict(r) for r in con.execute('''SELECT a.id,a.name,COUNT(o.id) AS opportunities,
                COALESCE(SUM(CASE WHEN o.stage NOT IN ('Won','Lost') THEN o.amount_cents ELSE 0 END),0) AS open_cents
                FROM accounts a LEFT JOIN opportunities o ON a.id=o.account_id GROUP BY a.id ORDER BY a.name''')]
            weighted=sum((r['amount_cents']*STAGES[r['stage']]+50)//100 for r in con.execute("SELECT amount_cents,stage FROM opportunities WHERE stage NOT IN ('Won','Lost')"))
        return {'stages': rows, 'accounts': accounts, 'overdue': overdue,
                'open_cents': sum(r['value_cents'] for r in rows if r['stage'] not in ('Won','Lost')),
                'weighted_cents': weighted,
                'won_cents': sum(r['value_cents'] for r in rows if r['stage']=='Won')}

    def csv(self):
        workspace = self.workspace();accounts = {x['id']: x['name'] for x in workspace['accounts']}
        stream = io.StringIO();writer = csv.writer(stream)
        writer.writerow(['Opportunity','Account','Stage','Value USD','Next action','Due'])
        def safe(value):
            value = str(value)
            return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) else value
        for row in workspace['opportunities']:
            writer.writerow([safe(row['title']),safe(accounts[row['account_id']]),row['stage'],
                f'{row["amount_cents"]/100:.2f}',safe(row['next_action']),row['due_date']])
        return stream.getvalue()

    def seed(self):
        if self.workspace()['accounts']:
            return
        fixtures = [('Northstar Labs','northstar.test','Mina Chen','mina@northstar.test'),
                    ('Harbor Design','harbor.test','Sam Ortiz','sam@harbor.test'),
                    ('Cedar Health','cedar.test','Alex Park','alex@cedar.test')]
        for i,(name,domain,person,email) in enumerate(fixtures,1):
            a=self.mutate('accounts',dict(name=name,domain=domain),f'seed-account-{i}')
            self.mutate('contacts',dict(account_id=a['id'],name=person,email=email),f'seed-contact-{i}')
        for i,(title,amount,stage,action,due_date) in enumerate([
            ('Platform renewal',4800000,'Proposal','Review procurement feedback','2026-10-29'),
            ('Team expansion',1800000,'Qualified','Confirm seats with sponsor','2026-10-28'),
            ('Research pilot',950000,'Lead','Schedule discovery conversation','2026-10-30'),
            ('Annual service agreement',3200000,'Proposal','Send commercial summary','2026-10-27'),
            ('Regional rollout',7400000,'Qualified','Discuss implementation timeline','2026-11-03'),
            ('Analytics evaluation',1250000,'Lead','Review discovery notes','2026-10-26')],1):
            account=(i-1)%3+1
            op=self.mutate('opportunities',dict(account_id=account,contact_id=account,title=title,amount_cents=amount,next_action=action,due_date=due_date),f'seed-op-{i}')
            if stage!='Lead':op=self.mutate('update',dict(id=op['id'],version=op['version'],stage='Qualified'),f'seed-q-{i}')
            if stage=='Proposal':self.mutate('update',dict(id=op['id'],version=op['version'],stage='Proposal'),f'seed-p-{i}')
