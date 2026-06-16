import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import xml.etree.ElementTree as ET
import os
import re
import sys

NS = 'http://www.lotus.com/dxl'

def tag(name):
    return f'{{{NS}}}{name}'

def clean_xml(content):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)

PASTE_TRIGGERS  = {'afterdocumentispasted', 'paste', 'pasted'}
SCHED_TRIGGERS  = {'scheduled'}
WANTED_TRIGGERS = PASTE_TRIGGERS | SCHED_TRIGGERS

PA_TYPE_MAP = {
    'text':     'Single-line text',
    'richtext': 'Multi-line text / Attachment',
    'number':   'Number',
    'datetime': 'Date/Time',
    'keyword':  'Choice',
    'names':    'Person or Lookup',
    'authors':  'Person (Editor/Creator)',
    'readers':  'Person group (visibility)',
}


class NSFAnalyzer:
    def __init__(self, root):
        self.root = root
        self.root.title('NSF DXL Analyzer')
        self.root.geometry('1300x780')
        self.root.minsize(900, 550)
        self.xml_data   = None
        self.xml_path   = None
        self.tree_items = {}
        self._usage_cache  = None
        self._filter_cache = None
        self._build_ui()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        tb = tk.Frame(self.root, bd=1, relief=tk.RAISED, padx=4, pady=3)
        tb.pack(side=tk.TOP, fill=tk.X)

        tk.Button(tb, text='Open XML',        width=10, command=self.open_xml).pack(side=tk.LEFT, padx=2)
        tk.Button(tb, text='Open Folder',     width=10, command=self.open_folder).pack(side=tk.LEFT, padx=2)
        tk.Button(tb, text='Generate Report', width=14, command=self.generate_report).pack(side=tk.LEFT, padx=2)

        tk.Label(tb, text='  Search field:').pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        se = tk.Entry(tb, textvariable=self.search_var, width=22)
        se.pack(side=tk.LEFT, padx=2)
        se.bind('<Return>', lambda e: self.do_search())
        tk.Button(tb, text='Find',  width=6, command=self.do_search).pack(side=tk.LEFT, padx=2)
        tk.Button(tb, text='Clear', width=6, command=self.clear_search).pack(side=tk.LEFT)

        self.db_label = tk.Label(tb, text='No file loaded', fg='gray', anchor=tk.W)
        self.db_label.pack(side=tk.LEFT, padx=12)

        paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashwidth=5, sashrelief=tk.RAISED)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = tk.Frame(paned)
        paned.add(left, minsize=240)
        self.tree = ttk.Treeview(left, selectmode='browse')
        self.tree.heading('#0', text='Structure', anchor=tk.W)
        vsb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        right = tk.Frame(paned)
        paned.add(right, minsize=620)
        nb = ttk.Notebook(right)
        nb.pack(fill=tk.BOTH, expand=True)
        self.nb = nb

        f1 = tk.Frame(nb); nb.add(f1, text='Overview')
        self.ov_text = self._make_text(f1, font=('Consolas', 10), wrap=tk.WORD)

        f2 = tk.Frame(nb); nb.add(f2, text='Fields')
        self.fld_label = tk.Label(f2, text='', anchor=tk.W, fg='gray')
        self.fld_label.pack(anchor=tk.W, padx=4, pady=2)
        cols = ('Field Name', 'Type', 'Kind')
        self.fld_tree = ttk.Treeview(f2, columns=cols, show='headings')
        for c, w in zip(cols, (300, 110, 110)):
            self.fld_tree.heading(c, text=c, anchor=tk.W)
            self.fld_tree.column(c, width=w, anchor=tk.W)
        fvs = ttk.Scrollbar(f2, orient='vertical', command=self.fld_tree.yview)
        self.fld_tree.configure(yscrollcommand=fvs.set)
        fvs.pack(side=tk.RIGHT, fill=tk.Y)
        self.fld_tree.pack(fill=tk.BOTH, expand=True)

        f3 = tk.Frame(nb); nb.add(f3, text='Usage & Flow')
        self.flow_text = self._make_text(f3, font=('Consolas', 10), wrap=tk.WORD)

        f4 = tk.Frame(nb); nb.add(f4, text='Code / Formulas')
        self.code_text = self._make_text(f4, font=('Consolas', 10),
                                          bg='#1e1e1e', fg='#d4d4d4',
                                          insertbackground='white', wrap=tk.NONE)

        f5 = tk.Frame(nb); nb.add(f5, text='Search Results')
        self.srch_text = self._make_text(f5, font=('Consolas', 10), wrap=tk.WORD)

        f6 = tk.Frame(nb); nb.add(f6, text='Inventory TXT')
        self.txt_text = self._make_text(f6, font=('Consolas', 10), wrap=tk.NONE)

        self.status = tk.StringVar(value='Ready')
        tk.Label(self.root, textvariable=self.status, bd=1, relief=tk.SUNKEN,
                 anchor=tk.W, padx=4).pack(side=tk.BOTTOM, fill=tk.X)

    def _make_text(self, parent, **kw):
        frame = tk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        xsb = ttk.Scrollbar(frame, orient='horizontal')
        ysb = ttk.Scrollbar(frame, orient='vertical')
        t = tk.Text(frame, yscrollcommand=ysb.set, xscrollcommand=xsb.set,
                    state=tk.DISABLED, **kw)
        ysb.configure(command=t.yview)
        xsb.configure(command=t.xview)
        xsb.pack(side=tk.BOTTOM, fill=tk.X)
        ysb.pack(side=tk.RIGHT,  fill=tk.Y)
        t.pack(fill=tk.BOTH, expand=True)
        return t

    # ── File loading ─────────────────────────────────────────────────────────

    def open_xml(self):
        path = filedialog.askopenfilename(
            title='Open DXL XML',
            filetypes=[('XML files', '*.xml')],
            initialdir=os.path.expanduser('~'))
        if not path:
            return
        self._load_xml(path)
        txt = path.replace('_dxl.xml', '_inventory.txt')
        if os.path.exists(txt):
            self._load_txt(txt)

    def open_folder(self):
        folder = filedialog.askdirectory(title='Select folder',
                                          initialdir=os.path.expanduser('~'))
        if not folder:
            return
        pairs = self._find_pairs(folder)
        if not pairs:
            messagebox.showinfo('Not found', 'No XML files found in this folder.')
            return
        if len(pairs) == 1:
            self._load_pair(*pairs[0])
        else:
            self._show_picker(pairs)

    def _find_pairs(self, folder):
        pairs = []
        for f in sorted(os.listdir(folder)):
            if f.endswith('.xml'):
                xml_path = os.path.join(folder, f)
                stem = f[:-4]
                txt_name = stem.replace('_dxl', '_inventory') + '.txt'
                txt_path = os.path.join(folder, txt_name)
                pairs.append((xml_path, txt_path if os.path.exists(txt_path) else None))
        return pairs

    def _load_pair(self, xml_path, txt_path):
        self._load_xml(xml_path)
        if txt_path:
            self._load_txt(txt_path)

    def _show_picker(self, pairs):
        win = tk.Toplevel(self.root)
        win.title('Select Database')
        win.geometry('520x320')
        win.grab_set()
        tk.Label(win, text='Select a database to load:', anchor=tk.W).pack(fill=tk.X, padx=8, pady=4)
        lb = tk.Listbox(win, font=('Consolas', 10))
        lb.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        for xml_path, _ in pairs:
            lb.insert(tk.END, os.path.basename(xml_path))
        lb.selection_set(0)
        def load():
            sel = lb.curselection()
            if sel:
                self._load_pair(*pairs[sel[0]])
            win.destroy()
        tk.Button(win, text='Load', width=12, command=load).pack(pady=6)
        lb.bind('<Double-Button-1>', lambda e: load())
        win.bind('<Return>', lambda e: load())

    def _load_xml(self, path):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                raw = f.read()
            raw = clean_xml(raw)
            root = ET.fromstring(raw)
            self.xml_data = self._parse_xml(root)
            self.xml_data['_path'] = path
            self.xml_path = path
            self._usage_cache  = None
            self._filter_cache = None
            self._populate_tree()
            title = self.xml_data.get('db_title') or os.path.basename(path)
            self.db_label.config(text=title, fg='black')
            self.status.set(f'XML loaded: {path}')
        except ET.ParseError as e:
            messagebox.showerror('XML Parse Error', f'Could not parse XML:\n{e}\n\nFile: {path}')
        except Exception as e:
            messagebox.showerror('Error', str(e))

    def _load_txt(self, path):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            self._write(self.txt_text, content)
            self.status.set(f'Loaded: {os.path.basename(self.xml_path)}  +  {os.path.basename(path)}')
        except Exception as e:
            messagebox.showerror('Error loading TXT', str(e))

    # ── XML parsing ───────────────────────────────────────────────────────────

    def _parse_xml(self, root):
        data = {
            'db_title':     root.get('title', ''),
            'db_path':      root.get('path', ''),
            'db_replicaid': root.get('replicaid', ''),
            'forms':    [],
            'subforms': [],
            'views':    [],
            'agents':   [],
            'pages':    [],
            'outlines': [],
        }
        dbinfo = root.find(tag('databaseinfo'))
        if dbinfo is not None:
            data['db_docs']      = dbinfo.get('numberofdocuments', '')
            data['db_diskspace'] = dbinfo.get('diskspace', '')
            data['db_percent']   = dbinfo.get('percentused', '')

        for elem in root.findall(tag('form')):
            data['forms'].append(self._parse_design(elem, 'form'))
        for elem in root.findall(tag('subform')):
            data['subforms'].append(self._parse_design(elem, 'subform'))
        for elem in root.findall(tag('page')):
            data['pages'].append(self._parse_page(elem))
        for elem in root.findall(tag('outline')):
            data['outlines'].append(self._parse_outline(elem))
        for elem in root.findall(tag('view')):
            data['views'].append(self._parse_view(elem))
        for elem in root.findall(tag('agent')):
            data['agents'].append(self._parse_agent(elem))
        return data

    def _parse_design(self, elem, etype):
        d = {
            'name':         elem.get('name', ''),
            'type':         etype,
            'is_default':   elem.get('default', 'false') == 'true',
            'fields':       [],
            'kw_fields':    {},
            'subform_refs': [],
            'actions':      [],
            'code':         [],
        }
        for f in elem.iter(tag('field')):
            fdata = {
                'name':          f.get('name', ''),
                'type':          f.get('type', 'text'),
                'kind':          f.get('kind', 'editable'),
                'formula':       '',
                'formula_event': '',
            }
            d['fields'].append(fdata)
            if fdata['type'] == 'keyword':
                choices = []
                kw = f.find(tag('keywords'))   # DXL uses <keywords> plural
                if kw is not None:
                    tl = kw.find(tag('textlist'))
                    src = tl if tl is not None else kw
                    for txt in src.findall(tag('text')):
                        if txt.text:
                            choices.append(txt.text.strip())
                # Only store if we got choices; empty means view-lookup driven
                if choices:
                    # Keep first non-empty definition (field may appear twice in DXL)
                    if fdata['name'] not in d['kw_fields']:
                        d['kw_fields'][fdata['name']] = choices
                elif kw is not None:
                    # <keywords> present but empty → choices come from a view lookup
                    if fdata['name'] not in d['kw_fields']:
                        d['kw_fields'][fdata['name']] = ['(choices from view lookup)']
            # Per-field formula (defaultvalue / value / inputtranslation)
            for code_elem in f.findall(tag('code')):
                ev = code_elem.get('event', '')
                if ev in ('defaultvalue', 'value', 'inputtranslation'):
                    fml = code_elem.find(tag('formula'))
                    ls_e = code_elem.find(tag('lotusscript'))
                    if fml is not None and fml.text:
                        fdata['formula'] = fml.text.strip()
                        fdata['formula_event'] = ev
                        break
                    elif ls_e is not None and ls_e.text:
                        fdata['formula'] = ls_e.text.strip()
                        fdata['formula_event'] = ev
                        break

        for ref in elem.iter(tag('subformref')):
            name = ref.get('name', '')
            if name and name not in d['subform_refs']:
                d['subform_refs'].append(name)

        ab = elem.find(tag('actionbar'))
        if ab is not None:
            for action in ab.findall(tag('action')):
                if action.get('systemcommand'):
                    continue
                title = action.get('title', '').replace('_', '')
                act = {'title': title, 'code': '', 'lang': '', 'hidewhen': ''}
                for code in action.findall(tag('code')):
                    ev  = code.get('event', '')
                    ls  = code.find(tag('lotusscript'))
                    fml = code.find(tag('formula'))
                    if ev == 'hidewhen':
                        if fml is not None and fml.text:
                            act['hidewhen'] = fml.text.strip()
                    else:
                        if ls is not None and ls.text and not act['code']:
                            act['code'] = ls.text.strip()
                            act['lang'] = 'lotusscript'
                        elif fml is not None and fml.text and not act['code']:
                            act['code'] = fml.text.strip()
                            act['lang'] = 'formula'
                if act['title']:
                    d['actions'].append(act)

        for code in elem.iter(tag('code')):
            event   = code.get('event', '')
            formula = code.find(tag('formula'))
            ls      = code.find(tag('lotusscript'))
            if formula is not None and formula.text:
                d['code'].append((event, 'formula', formula.text.strip()))
            elif ls is not None and ls.text:
                d['code'].append((event, 'lotusscript', ls.text.strip()))
        return d

    def _parse_page(self, elem):
        p = {'name': elem.get('name', ''), 'form_refs': [], 'view_refs': []}
        ab = elem.find(tag('actionbar'))
        if ab is not None:
            for action in ab.findall(tag('action')):
                code_text = ''
                for code in action.iter(tag('code')):
                    fml = code.find(tag('formula'))
                    ls  = code.find(tag('lotusscript'))
                    if fml is not None and fml.text:
                        code_text += fml.text
                    elif ls is not None and ls.text:
                        code_text += ls.text
                for m in re.findall(r'\[Compose\]\s*;\s*["\']([^"\']+)["\']', code_text, re.IGNORECASE):
                    if m not in p['form_refs']:
                        p['form_refs'].append(m)
                for m in re.findall(r'\[OpenView\]\s*;\s*["\']([^"\']+)["\']', code_text, re.IGNORECASE):
                    if m not in p['view_refs']:
                        p['view_refs'].append(m)
        for ev in elem.iter(tag('embeddedview')):
            vname = ev.get('name', '')
            if vname and vname not in p['view_refs']:
                p['view_refs'].append(vname)
        for code in elem.iter(tag('code')):
            fml = code.find(tag('formula'))
            ls  = code.find(tag('lotusscript'))
            code_text = ''
            if fml is not None and fml.text:
                code_text = fml.text
            elif ls is not None and ls.text:
                code_text = ls.text
            for m in re.findall(r'\[Compose\]\s*;\s*["\']([^"\']+)["\']', code_text, re.IGNORECASE):
                if m not in p['form_refs']:
                    p['form_refs'].append(m)
            for m in re.findall(r'\[OpenView\]\s*;\s*["\']([^"\']+)["\']', code_text, re.IGNORECASE):
                if m not in p['view_refs']:
                    p['view_refs'].append(m)
        return p

    def _parse_outline(self, elem):
        o = {'name': elem.get('name', ''), 'form_refs': [], 'view_refs': []}
        for entry in elem.iter(tag('outlineentry')):
            nl = entry.find(tag('namedlink'))
            if nl is not None:
                fe = nl.find(tag('form'))
                ve = nl.find(tag('view'))
                if fe is not None and fe.get('name') and fe.get('name') not in o['form_refs']:
                    o['form_refs'].append(fe.get('name'))
                if ve is not None and ve.get('name') and ve.get('name') not in o['view_refs']:
                    o['view_refs'].append(ve.get('name'))
            url_elem = entry.find(tag('url'))
            if url_elem is not None and url_elem.text:
                url = url_elem.text
                for m in re.findall(r'\?OpenView[^&]*&([^&\s]+)', url, re.IGNORECASE):
                    if m not in o['view_refs']:
                        o['view_refs'].append(m)
                for m in re.findall(r'Form=([^&\s]+)', url, re.IGNORECASE):
                    if m not in o['form_refs']:
                        o['form_refs'].append(m)
        return o

    def _parse_view(self, elem):
        v = {
            'name':      elem.get('name', ''),
            'is_folder': elem.get('folder', 'false') == 'true',
            'in_menu':   elem.get('showinmenu', 'true') != 'false',
            'selection': '',
            'columns':   [],
        }
        for code in elem.iter(tag('code')):
            if code.get('event') == 'selection':
                f = code.find(tag('formula'))
                if f is not None and f.text:
                    v['selection'] = f.text.strip()
        for col in elem.findall(tag('column')):
            c = {'title': '', 'field': col.get('itemname', ''), 'formula': '',
                 'sorted': col.get('sort', '') != ''}
            hdr = col.find(tag('columnheader'))
            if hdr is not None:
                c['title'] = hdr.get('title', '')
            for code in col.findall(tag('code')):
                if code.get('event') == 'value':
                    f = code.find(tag('formula'))
                    if f is not None and f.text:
                        c['formula'] = f.text.strip()
            v['columns'].append(c)
        return v

    def _parse_agent(self, elem):
        a = {
            'name':           elem.get('name', ''),
            'comment':        elem.get('comment', ''),
            'trigger_type':   '',
            'trigger_detail': '',
            'code':           [],
        }
        trigger_elem = elem.find(tag('trigger'))
        if trigger_elem is not None:
            a['trigger_type'] = trigger_elem.get('type', '').lower()
            sched = trigger_elem.find(tag('schedule'))
            if sched is not None:
                freq   = sched.get('type', '')
                server = sched.get('runserver', '')
                a['trigger_detail'] = f"{freq}  server: {server}" if server else freq
        for code in elem.iter(tag('code')):
            event = code.get('event', '')
            ls    = code.find(tag('lotusscript'))
            fml   = code.find(tag('formula'))
            if ls is not None and ls.text:
                a['code'].append((event, 'lotusscript', ls.text.strip()))
            elif fml is not None and fml.text:
                a['code'].append((event, 'formula', fml.text.strip()))
        return a

    # ── Filtering ─────────────────────────────────────────────────────────────

    def _apply_filters(self):
        if self._filter_cache is not None:
            return self._filter_cache
        d = self.xml_data

        ref_forms = set()
        ref_views = set()
        has_nav   = False

        for page in d.get('pages', []):
            if page['form_refs'] or page['view_refs']:
                has_nav = True
            ref_forms.update(page['form_refs'])
            ref_views.update(page['view_refs'])

        for outline in d.get('outlines', []):
            if outline['form_refs'] or outline['view_refs']:
                has_nav = True
            ref_forms.update(outline['form_refs'])
            ref_views.update(outline['view_refs'])

        if not has_nav:
            for view in d['views']:
                sel = view['selection']
                for m in re.findall(r'Form\s*[=!]=?\s*["\']([^"\']+)["\']', sel, re.IGNORECASE):
                    ref_forms.add(m)
                if view['in_menu']:
                    ref_views.add(view['name'])

        for form in d['forms']:
            if form['is_default']:
                ref_forms.add(form['name'])

        all_form_names = {f['name'] for f in d['forms']}
        matched_forms  = ref_forms & all_form_names
        filtered_forms = [f for f in d['forms'] if f['name'] in matched_forms] \
                         if matched_forms else d['forms']

        if ref_views:
            filtered_views = [v for v in d['views'] if v['name'] in ref_views]
        else:
            filtered_form_names = {f['name'] for f in filtered_forms}
            filtered_views = []
            for v in d['views']:
                sel_forms = re.findall(r'Form\s*[=!]=?\s*["\']([^"\']+)["\']',
                                       v['selection'], re.IGNORECASE)
                if any(fn in filtered_form_names for fn in sel_forms) or v['in_menu']:
                    filtered_views.append(v)
            if not filtered_views:
                filtered_views = d['views']

        used_sf = set()
        for form in filtered_forms:
            used_sf.update(form.get('subform_refs', []))
        filtered_subforms = [sf for sf in d['subforms'] if sf['name'] in used_sf]

        filtered_agents = [a for a in d['agents']
                           if a['trigger_type'].lower() in WANTED_TRIGGERS]

        self._filter_cache = {
            'forms':    filtered_forms,
            'subforms': filtered_subforms,
            'views':    filtered_views,
            'agents':   filtered_agents,
            'has_nav':  has_nav,
        }
        return self._filter_cache

    # ── Usage analysis ────────────────────────────────────────────────────────

    def _build_usage(self):
        if self._usage_cache is not None:
            return self._usage_cache
        d = self.xml_data
        usage = {f['name']: {'views': [], 'agents': [], 'pages': [], 'outlines': []}
                 for f in d['forms']}

        for view in d['views']:
            for m in re.findall(r'Form\s*[=!]=?\s*["\']([^"\']+)["\']',
                                view['selection'], re.IGNORECASE):
                if m in usage and view['name'] not in usage[m]['views']:
                    usage[m]['views'].append(view['name'])

        for agent in d['agents']:
            for _, _, code in agent['code']:
                refs = set(re.findall(r'Form\s*[=!]=?\s*["\']([^"\']+)["\']', code, re.IGNORECASE))
                refs |= set(re.findall(r'ReplaceItemValue\s*\(["\']Form["\'],?\s*["\']([^"\']+)["\']',
                                       code, re.IGNORECASE))
                for m in refs:
                    if m in usage and agent['name'] not in usage[m]['agents']:
                        usage[m]['agents'].append(agent['name'])

        for page in d.get('pages', []):
            for fn in page['form_refs']:
                if fn in usage and page['name'] not in usage[fn]['pages']:
                    usage[fn]['pages'].append(page['name'])

        for outline in d.get('outlines', []):
            for fn in outline['form_refs']:
                if fn in usage and outline['name'] not in usage[fn]['outlines']:
                    usage[fn]['outlines'].append(outline['name'])

        self._usage_cache = usage
        return usage

    @staticmethod
    def _extract_field_sets(code):
        """
        Extract all FIELD X := value assignments from a Notes formula.
        Captures both string literals and @formula/@UserName/@Now values.
        Stops at ) ; or newline so @If(...;FIELD CA:="X") doesn't bleed.
        Deduplicates — keeps last value assigned to each field name.
        """
        sets_map = {}
        # FIELD X := value — stop at ) ; or end-of-line
        for m in re.finditer(r'FIELD\s+(\w+)\s*:=\s*(.+?)(?=\s*[);]|\s*\n|\s*$)',
                             code, re.IGNORECASE | re.MULTILINE):
            fname = m.group(1).strip()
            val   = m.group(2).strip().strip('"\'')
            sets_map[fname] = val
        # @SetField("name"; value)
        for fname, val in re.findall(
                r'@SetField\s*\(\s*["\'](\w+)["\'];\s*(.+?)(?=\s*\))', code, re.IGNORECASE):
            sets_map[fname.strip()] = val.strip().strip('"\'')
        # ReplaceItemValue("name", value)
        for fname, val in re.findall(
                r'ReplaceItemValue\s*\(["\'](\w+)["\'],\s*(.+?)(?=\s*\))', code, re.IGNORECASE):
            sets_map[fname.strip()] = val.strip().strip('"\'')
        return [k + ' := ' + v for k, v in sets_map.items()]

    @staticmethod
    def _extract_validations(code):
        """
        Extract required-field validation checks from Notes formula.
        Handles both:
          @If(Field = ""; @Return(@Do(...@Prompt([Ok];"Alert!";"msg")));"")
          @If(cond & Field = ""; @Return(...@Prompt(..."msg"...)))
        Returns list of (field_name, message) tuples.
        """
        results = []
        seen = set()
        # Match @If(...fieldname=""...; ... @Prompt(..."message"...)
        # Uses .*? with DOTALL so it can cross internal semicolons
        for m in re.finditer(
                r'@If\s*\([^;]*?(\w+)\s*=\s*""[^;]*?;'
                r'.*?@Prompt\s*\([^;]+;[^;]+;\s*"([^"]+)"',
                code, re.IGNORECASE | re.DOTALL):
            field = m.group(1)
            msg   = m.group(2)
            if field not in seen:
                seen.add(field)
                results.append((field, msg))
        return results

    def _extract_flow(self, form):
        flow = {'status_fields': form.get('kw_fields', {}), 'actions': []}
        for action in form.get('actions', []):
            code = action.get('code', '')
            act  = {
                'title':       action['title'],
                'lang':        action['lang'],
                'hidewhen':    action.get('hidewhen', ''),
                'sets':        self._extract_field_sets(code),
                'validations': self._extract_validations(code),
                'notifies':    [],
                'saves':       False,
                'deletes':     False,
                'full_code':   code,
            }
            for m in re.findall(r'@MailSend\s*\(\s*([^;)]+)', code, re.IGNORECASE):
                r = m.strip().strip('"\'')
                if r and r not in act['notifies']:
                    act['notifies'].append(r)
            if re.search(r'\.Send\s*\(', code, re.IGNORECASE):
                act['notifies'].append('(LotusScript .Send)')
            act['saves']   = bool(re.search(r'@Save|\.Save\s*\(|uidoc\.Save', code, re.IGNORECASE))
            act['deletes'] = bool(re.search(r'@DeleteDocument|\.Delete|@Command\(\[Clear\]', code, re.IGNORECASE))
            flow['actions'].append(act)
        return flow

    # ── Process flow narrative ────────────────────────────────────────────────

    def _build_process_flow_narrative(self, form):
        """
        Builds a stage-by-stage process flow from CA choices + action button
        hidewhen conditions + editor field formulas.
        Returns list of text lines.
        """
        lines = []
        kw = form.get('kw_fields', {})

        # Pick primary status field (prefer CA)
        status_field   = 'CA' if 'CA' in kw else (next(iter(kw)) if kw else None)
        status_choices = kw.get(status_field, []) if status_field else []

        # Parse transitions from action buttons
        transitions = []
        for act in form.get('actions', []):
            code = act.get('code', '')
            notifies = []

            # Use the same broad field-set extractor as _extract_flow
            all_sets = self._extract_field_sets(code)
            # Build a dict: last value wins per field name
            sets_dict = {}
            for entry in all_sets:
                if ' := ' in entry:
                    k, v = entry.split(' := ', 1)
                    sets_dict[k.strip()] = v.strip()

            for m in re.findall(r'@MailSend\s*\(\s*([^;)]+)', code, re.IGNORECASE):
                r = m.strip().strip('"\'')
                if r:
                    notifies.append(r)
            if re.search(r'\.Send\s*\(', code, re.IGNORECASE):
                notifies.append('.Send()')

            # Infer from-state from hidewhen formula
            hw = act.get('hidewhen', '')
            from_states = set()
            if hw:
                not_states = re.findall(r'CA\s*!=\s*["\']([^"\']+)["\']', hw, re.IGNORECASE)
                eq_states  = re.findall(r'CA\s*=\s*["\']([^"\']+)["\']',  hw, re.IGNORECASE)
                if len(not_states) == 1 and not eq_states:
                    from_states.add(not_states[0])

            ca_val     = sets_dict.get('CA', sets_dict.get('ca', ''))
            other_sets = {k: v for k, v in sets_dict.items() if k.upper() != 'CA'}

            transitions.append({
                'title':       act['title'],
                'hidewhen':    hw,
                'ca_to':       ca_val,
                'other_sets':  other_sets,
                'notifies':    notifies,
                'from_states': from_states,
            })

        # Extract editor access rules from authors/names field formulas
        editor_by_state = {}
        for fld in form.get('fields', []):
            fname   = fld.get('name', '')
            formula = fld.get('formula', '')
            if not formula or fld.get('type') not in ('authors', 'names'):
                continue
            if 'CA' not in formula.upper():
                continue
            for m in re.finditer(r'CA\s*=\s*["\']([^"\']+)["\']', formula, re.IGNORECASE):
                state = m.group(1)
                editor_by_state.setdefault(state, [])
                if fname not in editor_by_state[state]:
                    editor_by_state[state].append(fname)

        # Status field header
        if status_field and status_choices:
            lines.append(f"  Status field [{status_field}] choices:")
            lines.append(f"    {' | '.join(status_choices)}")
            lines.append('')

        # Group transitions by from_state
        state_to_acts = {}
        ungrouped     = []
        for t in transitions:
            if t['from_states']:
                for fs in t['from_states']:
                    state_to_acts.setdefault(fs, []).append(t)
            else:
                ungrouped.append(t)

        # Write stage-by-stage using status_choices as ordering
        step = 1
        written_states = set()
        for state in (status_choices or []):
            acts_in = state_to_acts.get(state, [])
            editors = editor_by_state.get(state, [])
            if not acts_in and not editors:
                continue
            lines.append(f"  Stage {step}: CA = \"{state}\"")
            step += 1
            written_states.add(state)
            if editors:
                lines.append(f"    Edit access : {', '.join(editors)}")
            for t in acts_in:
                line = f"    [{t['title']}]"
                if t['ca_to']:
                    line += f"  →  CA = \"{t['ca_to']}\""
                if t['other_sets']:
                    other_str = ',  '.join(k + " = '" + v + "'" for k, v in t['other_sets'].items())
                    line += f"  |  {other_str}"
                lines.append(line)
                if t['notifies']:
                    lines.append(f"        notify  : {', '.join(t['notifies'])}")
            lines.append('')

        # States referenced in hidewhen but not in status_choices
        for state, acts in state_to_acts.items():
            if state in written_states:
                continue
            lines.append(f"  Stage {step}: CA = \"{state}\"  (not in choices list)")
            step += 1
            for t in acts:
                line = f"    [{t['title']}]"
                if t['ca_to']:
                    line += f"  ->  CA = \"{t['ca_to']}\""
                if t['other_sets']:
                    other_str = ',  '.join(k + " = '" + v + "'" for k, v in t['other_sets'].items())
                    line += f"  |  {other_str}"
                lines.append(line)
                if t['notifies']:
                    lines.append(f"        notify  : {', '.join(t['notifies'])}")
            lines.append('')

        # Ungrouped (no hidewhen → stage unknown)
        meaningful = [t for t in ungrouped if t['ca_to'] or t['notifies'] or t['other_sets']]
        if meaningful:
            lines.append('  Remaining actions (stage not determinable from hidewhen):')
            for t in meaningful:
                line = f"    [{t['title']}]"
                if t['ca_to']:
                    line += f"  ->  CA = \"{t['ca_to']}\""
                if t['other_sets']:
                    other_str = ',  '.join(k + " = '" + v + "'" for k, v in t['other_sets'].items())
                    line += f"  |  {other_str}"
                lines.append(line)
                if t['notifies']:
                    lines.append(f"        notify  : {', '.join(t['notifies'])}")
            lines.append('')

        # Access control summary for states not already written in stages
        remaining_editors = {k: v for k, v in editor_by_state.items()
                             if k not in written_states and not state_to_acts.get(k)}
        if remaining_editors:
            lines.append('  Additional access control (no transitions found for these states):')
            for state in sorted(remaining_editors):
                lines.append(f"    CA = \"{state}\"  →  {', '.join(remaining_editors[state])} can edit")
            lines.append('')

        return lines

    # ── Power Apps notes ──────────────────────────────────────────────────────

    def _get_pa_notes(self, form, fd):
        """Returns Power Apps / SharePoint migration notes for a form."""
        lines = []

        subforms_by_name = {sf['name']: sf for sf in fd.get('subforms', [])}
        all_fields = list(form.get('fields', []))
        for sfname in form.get('subform_refs', []):
            sf = subforms_by_name.get(sfname)
            if sf:
                for fld in sf.get('fields', []):
                    fld_copy = dict(fld)
                    fld_copy['_from_subform'] = sfname
                    all_fields.append(fld_copy)

        editable    = []
        computed    = []
        person_flds = []
        attach_flds = []

        for f in all_fields:
            fname = f['name']
            ftype = f['type']
            fkind = f['kind']
            origin = f"  (subform: {f['_from_subform']})" if f.get('_from_subform') else ''
            if ftype == 'richtext':
                attach_flds.append(f"{fname}{origin}")
            elif ftype in ('names', 'authors', 'readers'):
                person_flds.append(f"{fname} [{fkind}]{origin}")
            elif fkind in ('computed', 'computedwhencomposed', 'computedfordisplay'):
                computed.append(f"{fname} ({ftype}){origin}")
            else:
                sp_type = PA_TYPE_MAP.get(ftype, ftype)
                editable.append(f"{fname:<30} → {sp_type}{origin}")

        if editable:
            lines.append('  SharePoint columns (user-editable):')
            for c in editable:
                lines.append(f"    {c}")
        if person_flds:
            lines.append('  Person / People-picker fields:')
            for c in person_flds:
                lines.append(f"    {c}  → Person/Group column")
        if attach_flds:
            lines.append('  Rich-text / Attachment fields:')
            for c in attach_flds:
                lines.append(f"    {c}  → Attachment or Multi-line text (HTML)")
        if computed:
            lines.append('  Computed fields (Power Automate or Calculated column):')
            for c in computed:
                lines.append(f"    {c}")

        # Subform migration hint
        if form.get('subform_refs'):
            lines.append('  Subform migration:')
            for sfname in form['subform_refs']:
                sf = subforms_by_name.get(sfname)
                if sf:
                    fnames = ', '.join(f['name'] for f in sf['fields'])
                    lines.append(f"    {sfname}  → embed as columns OR separate linked list")
                    lines.append(f"      Fields: {fnames}")

        # Scheduled agents
        agents = fd.get('agents', [])
        if agents:
            lines.append('  Power Automate flows (replace scheduled agents):')
            for a in agents:
                sched = a.get('trigger_detail', '')
                comment = f"  — {a['comment']}" if a.get('comment') else ''
                lines.append(f"    {a['name']}  [{sched}]{comment}")

        return lines

    # ── Tree ─────────────────────────────────────────────────────────────────

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.tree_items.clear()
        d  = self.xml_data
        fd = self._apply_filters()

        root_id = self.tree.insert('', 'end', text=d.get('db_title', 'Database'), open=True)
        self.tree_items[root_id] = ('db', d)

        def section(parent, label, items, all_items, itype, label_fn):
            count_str = f"{len(items)}/{len(all_items)}" if len(items) != len(all_items) else str(len(items))
            sid = self.tree.insert(parent, 'end', text=f'{label} ({count_str})', open=True)
            self.tree_items[sid] = ('section', label.lower())
            for item in items:
                iid = self.tree.insert(sid, 'end', text=label_fn(item))
                self.tree_items[iid] = (itype, item)

        section(root_id, 'Forms',    fd['forms'],    d['forms'],    'form',
                lambda x: ('* ' if x['is_default'] else '  ') + x['name'])
        section(root_id, 'Subforms', fd['subforms'], d['subforms'], 'subform',
                lambda x: x['name'])
        section(root_id, 'Views',    fd['views'],    d['views'],    'view',
                lambda x: ('[F] ' if x['is_folder'] else '[V] ') + x['name'])
        section(root_id, 'Agents',   fd['agents'],   d['agents'],   'agent',
                lambda x: f"[{x['trigger_type'].upper()[:5]}] {x['name']}")

        nav_src = []
        if d.get('pages'):    nav_src.append(f"{len(d['pages'])} page(s)")
        if d.get('outlines'): nav_src.append(f"{len(d['outlines'])} outline(s)")
        if not fd['has_nav']:
            nav_src.append('fallback: view formulas')
        if nav_src:
            info_id = self.tree.insert(root_id, 'end', text=f"  filter source: {', '.join(nav_src)}")
            self.tree_items[info_id] = ('info', None)

    # ── Detail panels ─────────────────────────────────────────────────────────

    def _on_select(self, _event):
        sel = self.tree.selection()
        if not sel or sel[0] not in self.tree_items:
            return
        itype, idata = self.tree_items[sel[0]]
        if itype == 'info':
            return
        self._clear_detail()
        {'db': self._show_db, 'form': self._show_design, 'subform': self._show_design,
         'view': self._show_view, 'agent': self._show_agent,
         'section': self._show_section}.get(itype, lambda _: None)(idata)

    def _clear_detail(self):
        for t in (self.ov_text, self.flow_text, self.code_text):
            self._write(t, '')
        self.fld_tree.delete(*self.fld_tree.get_children())
        self.fld_label.config(text='')

    def _write(self, widget, text):
        widget.config(state=tk.NORMAL)
        widget.delete('1.0', tk.END)
        if text:
            widget.insert('1.0', text)
        widget.config(state=tk.DISABLED)

    def _show_db(self, d):
        fd = self._apply_filters()
        mb  = int(d.get('db_diskspace') or 0) // 1024 // 1024
        pct = d.get('db_percent', '')
        pct_str = f'{float(pct):.1f}%' if pct else ''
        lines = [
            f"DATABASE  : {d.get('db_title', '')}",
            f"PATH      : {d.get('db_path', '')}",
            f"REPLICA ID: {d.get('db_replicaid', '')}",
            f"DOCUMENTS : {d.get('db_docs', '')}",
            f"DISK SIZE : {mb} MB  {pct_str}",
            '',
            f"Forms     : {len(fd['forms'])}/{len(d['forms'])}  (used/total)",
            f"Subforms  : {len(fd['subforms'])}/{len(d['subforms'])}",
            f"Views     : {len(fd['views'])}/{len(d['views'])}",
            f"Agents    : {len(fd['agents'])}/{len(d['agents'])}  (scheduled/pasted only)",
            f"Pages     : {len(d.get('pages', []))}",
            f"Outlines  : {len(d.get('outlines', []))}",
            '',
        ]
        if not fd['has_nav']:
            lines.append('NOTE: No pages/outlines in DXL — filter based on view selection formulas.')
            lines.append('      Re-run agent with SelectPages=True + SelectOutlines=True for full filter.')
            lines.append('')
        lines.append('── Forms (used) ──────────────────────────────')
        for f in fd['forms']:
            refs = f"  refs: {', '.join(f['subform_refs'])}" if f['subform_refs'] else ''
            lines.append(f"  {'*' if f['is_default'] else ' '} {f['name']}  ({len(f['fields'])} fields){refs}")
        lines += ['', '── Subforms (used) ───────────────────────────']
        for sf in fd['subforms']:
            lines.append(f"  {sf['name']}  ({len(sf['fields'])} fields)")
        lines += ['', '── Agents (scheduled/pasted) ─────────────────']
        for a in fd['agents']:
            lines.append(f"  [{a['trigger_type']}]  {a['name']}")
            if a['trigger_detail']:
                lines.append(f"    {a['trigger_detail']}")
            if a.get('comment'):
                lines.append(f"    Note: {a['comment']}")
        self._write(self.ov_text, '\n'.join(lines))

        usage  = self._build_usage()
        flines = ['FORM USAGE SUMMARY\n']
        for form in fd['forms']:
            u = usage.get(form['name'], {})
            flines.append(f"  {'*' if form['is_default'] else ' '} {form['name']}")
            for src, key in [('Pages', 'pages'), ('Outlines', 'outlines'),
                              ('Views', 'views'), ('Agents', 'agents')]:
                if u.get(key):
                    flines.append(f"    {src:<10}: {', '.join(u[key])}")
            flines.append('')
        self._write(self.flow_text, '\n'.join(flines))

    def _show_design(self, d):
        label = 'FORM' if d['type'] == 'form' else 'SUBFORM'
        lines = [f"{label}: {d['name']}"]
        if d.get('is_default'):
            lines.append('  [DEFAULT FORM]')
        lines += [f"Fields  : {len(d['fields'])}", f"Actions : {len(d['actions'])}"]
        if d.get('subform_refs'):
            lines.append(f"Subforms: {', '.join(d['subform_refs'])}")
        if d['fields']:
            lines += ['', '── Fields ──────────────────────────────────────────────────────']
            hdr = f"  {'Field Name':<35} {'Type':<12} {'Kind':<22} Choices / Formula"
            lines.append(hdr)
            lines.append('  ' + '-' * 90)
            for f in d['fields']:
                note = ''
                if f['name'] in d.get('kw_fields', {}):
                    note = '  [' + ' | '.join(d['kw_fields'][f['name']]) + ']'
                elif f.get('formula'):
                    note  = f"  = {f['formula'].replace(chr(10), ' ')}"
                lines.append(f"  {f['name']:<35} {f['type']:<12} {f['kind']:<22}{note}")
        self._write(self.ov_text, '\n'.join(lines))

        self.fld_label.config(text=f"{label}: {d['name']}  —  {len(d['fields'])} field(s)")
        for f in d['fields']:
            self.fld_tree.insert('', 'end', values=(f['name'], f['type'], f['kind']))

        self._show_flow(d)

        if d['code']:
            parts = []
            for event, lang, text in d['code']:
                parts.append(f"' ── {event}  [{lang}] ─────────────────────────────")
                parts.append(text)
                parts.append('')
            self._write(self.code_text, '\n'.join(parts))

    def _show_flow(self, d):
        lines = [f"USAGE & PROCESS FLOW: {d['name']}", '']
        if d['type'] == 'form':
            usage = self._build_usage()
            u = usage.get(d['name'], {})
            lines.append('── WHERE THIS FORM IS USED ─────────────────────')
            for src, key in [('Pages', 'pages'), ('Outlines', 'outlines'),
                              ('Views', 'views'), ('Agents', 'agents')]:
                if u.get(key):
                    lines.append(f"  {src:<10}: {', '.join(u[key])}")
            if not any(u.get(k) for k in ('pages', 'outlines', 'views', 'agents')):
                lines.append('  (not found in any navigation element)')
            lines.append('')
            lines.append('── PROCESS FLOW ────────────────────────────────')
            lines += self._build_process_flow_narrative(d)

        kw = d.get('kw_fields', {})
        if kw:
            lines.append('── ALL KEYWORD FIELDS & CHOICES ────────────────')
            for fname, choices in kw.items():
                lines.append(f"  {fname}:")
                for c in choices:
                    lines.append(f"    • {c}")
            lines.append('')

        flow = self._extract_flow(d)
        if flow['actions']:
            lines.append('── ACTION BUTTONS ──────────────────────────────')
            for act in flow['actions']:
                lines.append(f"  [{act['title']}]  ({act['lang']})")
                if act['sets']:
                    lines.append(f"    Sets     : {', '.join(act['sets'])}")
                if act['notifies']:
                    lines.append(f"    Notifies : {', '.join(act['notifies'])}")
                if act['saves']:
                    lines.append(f"    Saves document")
                if act['deletes']:
                    lines.append(f"    Deletes document")
                lines.append('')
        self._write(self.flow_text, '\n'.join(lines))

    def _show_view(self, d):
        vtype = 'FOLDER' if d['is_folder'] else 'VIEW'
        lines = [f"{vtype}: {d['name']}", f"Columns: {len(d['columns'])}",
                 f"In menu: {'Yes' if d['in_menu'] else 'No'}"]
        if d['selection']:
            lines += ['', 'SELECTION:', f"  {d['selection']}"]
        if d['columns']:
            lines += ['', '── Columns ─────────────────────────────────────']
            for c in d['columns']:
                title  = c['title'] or '(untitled)'
                detail = f"field={c['field']}" if c['field'] else (f"formula={c['formula'][:50]}" if c['formula'] else '')
                lines.append(f"  {title:<30} {detail}{'  [sorted]' if c['sorted'] else ''}")
        self._write(self.ov_text, '\n'.join(lines))
        self.fld_label.config(text=f"{vtype}: {d['name']}  —  {len(d['columns'])} column(s)")
        for c in d['columns']:
            detail = c['field'] or (c['formula'][:60] if c['formula'] else '')
            self.fld_tree.insert('', 'end', values=(c['title'] or '(untitled)', detail,
                                                     'sorted' if c['sorted'] else ''))
        sel_forms = re.findall(r'Form\s*[=!]=?\s*["\']([^"\']+)["\']', d['selection'], re.IGNORECASE)
        flines = [f"VIEW: {d['name']}", '']
        if sel_forms:
            flines += ['── FORMS IN THIS VIEW ──────────────────────────'] + [f"  {fn}" for fn in sel_forms]
        else:
            flines.append('No form filter in selection formula.')
        self._write(self.flow_text, '\n'.join(flines))

    def _show_agent(self, d):
        trigger_label = {
            'scheduled':             'Scheduled',
            'afterdocumentispasted': 'After Document Pasted',
            'actionsmenu':           'Manual (Actions Menu)',
            'onnewdocumentcreate':   'On New Document',
            'onexistingdocumentupdate': 'On Document Update',
        }.get(d['trigger_type'], d['trigger_type'] or '(not set)')
        lines = [f"AGENT  : {d['name']}", f"Trigger: {trigger_label}"]
        if d['trigger_detail']:
            lines.append(f"Detail : {d['trigger_detail']}")
        if d.get('comment'):
            lines.append(f"Comment: {d['comment']}")
        self._write(self.ov_text, '\n'.join(lines))

        form_refs = set()
        for _, _, code in d['code']:
            form_refs |= set(re.findall(r'Form\s*[=!]=?\s*["\']([^"\']+)["\']', code, re.IGNORECASE))
        flines = [f"AGENT: {d['name']}", '']
        if form_refs:
            flines += ['── FORMS REFERENCED ────────────────────────────'] + [f"  {fn}" for fn in sorted(form_refs)]
        else:
            flines.append('No direct form references found in agent code.')
        self._write(self.flow_text, '\n'.join(flines))

        if d['code']:
            parts = []
            for event, lang, text in d['code']:
                parts.append(f"' ── {event}  [{lang}] ─────────────────────────────")
                parts.append(text)
                parts.append('')
            self._write(self.code_text, '\n'.join(parts))

    def _show_section(self, section):
        fd = self._apply_filters()
        d  = self.xml_data
        lines = []
        if section == 'forms':
            usage = self._build_usage()
            lines.append(f"FORMS  ({len(fd['forms'])}/{len(d['forms'])} used)\n")
            for f in fd['forms']:
                u = usage.get(f['name'], {})
                lines.append(f"  {'*' if f['is_default'] else ' '} {f['name']}")
                lines.append(f"    Fields   : {len(f['fields'])}")
                if f['subform_refs']:
                    lines.append(f"    Subforms : {', '.join(f['subform_refs'])}")
                for src, key in [('Pages', 'pages'), ('Outlines', 'outlines'),
                                  ('Views', 'views'), ('Agents', 'agents')]:
                    if u.get(key):
                        lines.append(f"    {src:<10}: {', '.join(u[key])}")
                lines.append('')
        elif section == 'subforms':
            lines.append(f"SUBFORMS  ({len(fd['subforms'])}/{len(d['subforms'])} used)\n")
            for sf in fd['subforms']:
                lines.append(f"  {sf['name']}  ({len(sf['fields'])} fields)")
                for fld in sf['fields']:
                    lines.append(f"    - {fld['name']}  ({fld['type']}, {fld['kind']})")
                lines.append('')
        elif section == 'views':
            lines.append(f"VIEWS  ({len(fd['views'])}/{len(d['views'])} used)\n")
            for v in fd['views']:
                vt = '[F]' if v['is_folder'] else '[V]'
                lines.append(f"  {vt} {v['name']}  —  {len(v['columns'])} columns")
                if v['selection']:
                    lines.append(f"       {v['selection']}")
        elif section == 'agents':
            lines.append(f"AGENTS  ({len(fd['agents'])}/{len(d['agents'])} — scheduled/pasted only)\n")
            for a in fd['agents']:
                lines.append(f"  [{a['trigger_type']}]  {a['name']}")
                if a['trigger_detail']:
                    lines.append(f"    {a['trigger_detail']}")
                if a.get('comment'):
                    lines.append(f"    Note: {a['comment']}")
        self._write(self.ov_text, '\n'.join(lines))

    # ── Report ────────────────────────────────────────────────────────────────

    def _build_report_text(self):
        d     = self.xml_data
        fd    = self._apply_filters()
        usage = self._build_usage()
        sep   = '=' * 60
        thin  = '-' * 60
        mb    = int(d.get('db_diskspace') or 0) // 1024 // 1024
        pct   = d.get('db_percent', '')
        pct_str = f'{float(pct):.1f}%' if pct else ''

        lines = [
            sep, 'NSF DXL ANALYSIS REPORT', sep,
            f"DATABASE  : {d.get('db_title', '')}",
            f"PATH      : {d.get('db_path', '')}",
            f"REPLICA ID: {d.get('db_replicaid', '')}",
            f"DOCUMENTS : {d.get('db_docs', '')}",
            f"DISK SIZE : {mb} MB  {pct_str}",
            f"SOURCE    : {d.get('_path', '')}",
            '',
            'SUMMARY (filtered/total):',
            f"  Forms    : {len(fd['forms'])}/{len(d['forms'])}",
            f"  Subforms : {len(fd['subforms'])}/{len(d['subforms'])}",
            f"  Views    : {len(fd['views'])}/{len(d['views'])}",
            f"  Agents   : {len(fd['agents'])}/{len(d['agents'])}  (scheduled/pasted trigger)",
            f"  Filter   : {'pages/outlines' if fd['has_nav'] else 'view selection formulas (no pages/outlines in DXL)'}",
            '',
        ]

        # ── Form usage summary ─────────────────────────────────────────────
        lines += [sep, 'FORM USAGE SUMMARY', sep, '']
        for form in fd['forms']:
            u = usage.get(form['name'], {})
            lines.append(f"  {'*' if form['is_default'] else ' '} {form['name']}")
            for src, key in [('Pages', 'pages'), ('Outlines', 'outlines'),
                              ('Views', 'views'), ('Agents', 'agents')]:
                if u.get(key):
                    lines.append(f"    {src:<10}: {', '.join(u[key])}")
            lines.append('')

        # ── Forms ─────────────────────────────────────────────────────────
        lines += [sep, 'FORMS', sep, '']
        for form in fd['forms']:
            u = usage.get(form['name'], {})
            lines.append(f"FORM: {form['name']}{'  [DEFAULT]' if form['is_default'] else ''}")
            lines.append(f"  Fields   : {len(form['fields'])}")
            lines.append(f"  Actions  : {len(form['actions'])}")
            if form['subform_refs']:
                lines.append(f"  Subforms : {', '.join(form['subform_refs'])}")
            for src, key in [('Pages', 'pages'), ('Outlines', 'outlines'),
                              ('Views', 'views'), ('Agents', 'agents')]:
                if u.get(key):
                    lines.append(f"  {src:<10}: {', '.join(u[key])}")
            lines.append('')

            # -- Data model (fields with choices / formulas inline) --
            lines.append(f"  {'─'*56}")
            lines.append('  DATA MODEL')
            lines.append(f"  {'─'*56}")
            hdr = f"  {'Field Name':<35} {'Type':<14} {'Kind':<24} Choices / Formula (truncated — see full below)"
            lines.append(hdr)
            lines.append('  ' + thin)
            for f in form['fields']:
                note = ''
                if f['name'] in form.get('kw_fields', {}):
                    choices = form['kw_fields'][f['name']]
                    note = '[' + ' | '.join(choices) + ']'
                elif f.get('formula'):
                    note  = f"= {f['formula'].replace(chr(10), ' ')}"
                lines.append(f"  {f['name']:<35} {f['type']:<14} {f['kind']:<24} {note}")
            lines.append('')

            # -- Computed field formulas: full text --
            has_formulas = [f for f in form['fields']
                            if f.get('formula') and f['name'] not in form.get('kw_fields', {})]
            if has_formulas:
                lines.append(f"  {'─'*56}")
                lines.append('  COMPUTED FIELD FORMULAS (full)')
                lines.append(f"  {'─'*56}")
                for f in has_formulas:
                    ev_tag = f"  [{f['formula_event']}]" if f.get('formula_event', 'defaultvalue') != 'defaultvalue' else ''
                    lines.append(f"  {f['name']}  ({f['type']}, {f['kind']}){ev_tag}:")
                    for fl in f['formula'].splitlines():
                        lines.append(f"    {fl}")
                    lines.append('')

            # -- Process flow --
            lines.append(f"  {'─'*56}")
            lines.append('  PROCESS FLOW')
            lines.append(f"  {'─'*56}")
            flow_lines = self._build_process_flow_narrative(form)
            if flow_lines:
                lines += flow_lines
            else:
                flow = self._extract_flow(form)
                for act in flow['actions']:
                    line = f"  [{act['title']}]"
                    if act['sets']:
                        line += f"  →  {', '.join(act['sets'])}"
                    lines.append(line)
                    if act['notifies']:
                        lines.append(f"      notify: {', '.join(act['notifies'])}")
                lines.append('')

            # -- Action buttons: validations + sets + notifies + full code --
            if form['actions']:
                lines.append(f"  {'─'*56}")
                lines.append('  ACTION BUTTONS')
                lines.append(f"  {'─'*56}")
                flow = self._extract_flow(form)
                for act in flow['actions']:
                    hw_label = ''
                    if act.get('hidewhen'):
                        hw_label = f"  [visible when: NOT ({act['hidewhen'][:60]})]"
                    lines.append(f"    [{act['title']}]{hw_label}")
                    if act['validations']:
                        lines.append('      Required fields:')
                        for fld, msg in act['validations']:
                            lines.append(f"        {fld}: {msg}")
                    if act['sets']:
                        lines.append(f"      Sets     : {', '.join(act['sets'])}")
                    if act['notifies']:
                        lines.append(f"      Notifies : {', '.join(act['notifies'])}")
                    if act['saves']:
                        lines.append('      Saves document')
                    if act['deletes']:
                        lines.append('      Deletes document')
                    # Full formula code (capped at 40 lines)
                    if act['full_code'] and act['lang']:
                        code_lines = act['full_code'].splitlines()
                        lines.append(f"      Formula ({act['lang']}):")
                        for cl in code_lines[:40]:
                            lines.append(f"        {cl}")
                        if len(code_lines) > 40:
                            lines.append(f"        ... ({len(code_lines) - 40} more lines)")
                    lines.append('')

            # -- Form-level code (querysave, postopen, inputvalidation, etc.) --
            # Exclude click (already shown in ACTION BUTTONS) and field/display events
            form_events = [(ev, lang, code) for ev, lang, code in form.get('code', [])
                           if ev not in ('', 'click', 'defaultvalue', 'value',
                                         'inputtranslation', 'hidewhen', 'windowtitle')]
            if form_events:
                lines.append(f"  {'─'*56}")
                lines.append('  FORM-LEVEL CODE (querysave / postopen / etc.)')
                lines.append(f"  {'─'*56}")
                for ev, lang, code in form_events:
                    lines.append(f"    event: {ev}  [{lang}]")
                    code_lines = code.splitlines()
                    for cl in code_lines[:50]:
                        lines.append(f"      {cl}")
                    if len(code_lines) > 50:
                        lines.append(f"      ... ({len(code_lines) - 50} more lines)")
                    lines.append('')

            # -- Power Apps notes --
            pa_lines = self._get_pa_notes(form, fd)
            if pa_lines:
                lines.append(f"  {'─'*56}")
                lines.append('  POWER APPS / MIGRATION NOTES')
                lines.append(f"  {'─'*56}")
                lines += pa_lines
                lines.append('')

            lines.append('')

        # ── Subforms ──────────────────────────────────────────────────────
        lines += [sep, 'SUBFORMS (used by filtered forms)', sep, '']
        for sf in fd['subforms']:
            n_act = len(sf.get('actions', []))
            lines.append(f"SUBFORM: {sf['name']}  ({len(sf['fields'])} fields, {n_act} action(s))")
            if sf['fields']:
                lines += ['', f"  {'Field Name':<35} {'Type':<14} {'Kind':<24} Choices / Formula",
                          '  ' + thin]
                for f in sf['fields']:
                    note = ''
                    if f['name'] in sf.get('kw_fields', {}):
                        note = '[' + ' | '.join(sf['kw_fields'][f['name']]) + ']'
                    elif f.get('formula'):
                        flat  = f['formula'].replace('\n', ' ')
                        short = flat
                        note  = f"= {short}"
                    lines.append(f"  {f['name']:<35} {f['type']:<14} {f['kind']:<24} {note}")
                # Full formulas for subform computed fields
                sf_formulas = [f for f in sf['fields']
                               if f.get('formula') and f['name'] not in sf.get('kw_fields', {})]
                if sf_formulas:
                    lines.append('')
                    lines.append('  Computed formulas (full):')
                    for f in sf_formulas:
                        lines.append(f"    {f['name']}:")
                        for fl in f['formula'].splitlines():
                            lines.append(f"      {fl}")

            # Action buttons (workflow buttons embedded in subform action bars)
            if sf.get('actions'):
                flow = self._extract_flow(sf)
                lines.append('')
                lines.append(f"  {'─'*56}")
                lines.append('  ACTION BUTTONS')
                lines.append(f"  {'─'*56}")
                for act in flow['actions']:
                    hw_label = ''
                    if act.get('hidewhen'):
                        hw_label = f"  [visible when: NOT ({act['hidewhen'][:60]})]"
                    lines.append(f"    [{act['title']}]{hw_label}")
                    if act['validations']:
                        lines.append('      Required fields:')
                        for fld, msg in act['validations']:
                            lines.append(f"        {fld}: {msg}")
                    if act['sets']:
                        lines.append(f"      Sets     : {', '.join(act['sets'])}")
                    if act['notifies']:
                        lines.append(f"      Notifies : {', '.join(act['notifies'])}")
                    if act['saves']:
                        lines.append('      Saves document')
                    if act['deletes']:
                        lines.append('      Deletes document')
                    if act['full_code'] and act['lang']:
                        code_lines = act['full_code'].splitlines()
                        lines.append(f"      Formula ({act['lang']}):")
                        for cl in code_lines[:40]:
                            lines.append(f"        {cl}")
                        if len(code_lines) > 40:
                            lines.append(f"        ... ({len(code_lines) - 40} more lines)")
                    lines.append('')

            lines.append('')

        # ── Views ─────────────────────────────────────────────────────────
        lines += [sep, 'VIEWS', sep, '']
        for v in fd['views']:
            vtype = 'FOLDER' if v['is_folder'] else 'VIEW'
            lines.append(f"{vtype}: {v['name']}  ({len(v['columns'])} columns)")
            if v['selection']:
                lines.append(f"  Selection : {v['selection']}")
            for c in v['columns']:
                title  = c['title'] or '(untitled)'
                detail = f"field={c['field']}" if c['field'] else (f"formula={c['formula'][:60]}" if c['formula'] else '')
                lines.append(f"    {title:<30} {detail}")
            lines.append('')

        # ── Agents ────────────────────────────────────────────────────────
        lines += [sep, 'AGENTS (scheduled / pasted trigger)', sep, '']
        for a in fd['agents']:
            trigger_label = {
                'scheduled':             'Scheduled',
                'afterdocumentispasted': 'After Document Pasted',
            }.get(a['trigger_type'], a['trigger_type'])
            lines.append(f"AGENT: {a['name']}  [{trigger_label}]")
            if a['trigger_detail']:
                lines.append(f"  Schedule : {a['trigger_detail']}")
            if a.get('comment'):
                lines.append(f"  Comment  : {a['comment']}")
            for event, lang, code in a['code']:
                lines.append(f"  ── {event} [{lang}] ──")
                for ln in code.splitlines()[:30]:
                    lines.append(f"    {ln}")
                if len(code.splitlines()) > 30:
                    lines.append(f"    ... ({len(code.splitlines()) - 30} more lines)")
            lines.append('')

        # ── Field cross-reference ─────────────────────────────────────────
        lines += [sep, 'FIELD CROSS-REFERENCE (filtered forms + subforms)', sep, '']
        all_fields_map = {}
        for item in fd['forms'] + fd['subforms']:
            for f in item['fields']:
                fn = f['name']
                entry = f"{item['type'].upper()}: {item['name']}"
                if fn not in all_fields_map:
                    all_fields_map[fn] = []
                all_fields_map[fn].append(entry)
        for fn in sorted(all_fields_map.keys()):
            lines.append(f"  {fn:<35} {', '.join(all_fields_map[fn])}")

        # ── Power Apps migration summary ──────────────────────────────────
        lines += ['', sep, 'POWER APPS MIGRATION SUMMARY', sep, '']
        lines.append('SharePoint List design (one list per main form):')
        for form in fd['forms']:
            if form['name'] == 'Reminder':
                continue
            lines.append(f"\n  List: {form['name']}")
            kw = form.get('kw_fields', {})
            status_field = 'CA' if 'CA' in kw else (next(iter(kw)) if kw else None)
            if status_field:
                choices = kw[status_field]
                lines.append(f"    Status column [{status_field}]: Choice  →  {' | '.join(choices)}")
            # Count field types
            type_counts = {}
            for f in form['fields']:
                ft = PA_TYPE_MAP.get(f['type'], f['type'])
                type_counts[ft] = type_counts.get(ft, 0) + 1
            for ft, cnt in sorted(type_counts.items()):
                lines.append(f"    {ft:<35} × {cnt}")

        lines += ['', 'Power Automate flows needed:']
        for a in fd['agents']:
            sched = a.get('trigger_detail', a.get('trigger_type', ''))
            comment = f" — {a['comment']}" if a.get('comment') else ''
            lines.append(f"  • {a['name']} [{sched}]{comment}")
            # Describe what view it queries
            for _, _, code in a['code']:
                views_used = re.findall(r'GetView\s*\(\s*["\']([^"\']+)["\']', code, re.IGNORECASE)
                if views_used:
                    lines.append(f"      queries view: {', '.join(views_used)}")
                    break

        lines += ['', sep]

        return '\n'.join(lines)

    def generate_report(self):
        if not self.xml_data:
            messagebox.showinfo('No data', 'Load an XML file first.')
            return
        d         = self.xml_data
        report    = self._build_report_text()
        default_name = os.path.basename(d.get('_path', 'report')).replace('_dxl.xml', '_report.txt')
        save_path = filedialog.asksaveasfilename(
            title='Save Report',
            defaultextension='.txt',
            filetypes=[('Text files', '*.txt')],
            initialdir=os.path.dirname(d.get('_path', os.path.expanduser('~'))),
            initialfile=default_name)
        if not save_path:
            return
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(report)
        self.status.set(f'Report saved: {save_path}')
        messagebox.showinfo('Done', f'Report saved:\n{save_path}')

    # ── Search ───────────────────────────────────────────────────────────────

    def do_search(self):
        query = self.search_var.get().strip().lower()
        if not query:
            return
        if not self.xml_data:
            messagebox.showinfo('No data', 'Load an XML file first.')
            return
        fd = self._apply_filters()
        results = []
        count = 0
        for item in fd['forms'] + fd['subforms']:
            label = 'FORM' if item['type'] == 'form' else 'SUBFORM'
            hits = [f for f in item['fields'] if query in f['name'].lower()]
            if hits:
                count += 1
                results.append(f"[{label}] {item['name']}")
                for h in hits:
                    results.append(f"    {h['name']:<35} {h['type']:<12} {h['kind']}")
                results.append('')
        if results:
            self._write(self.srch_text, f"Search: '{query}'  —  {count} element(s)\n\n" + '\n'.join(results))
        else:
            self._write(self.srch_text, f"Search: '{query}'\n\nNo matches found.")
        self.nb.select(4)

    def clear_search(self):
        self.search_var.set('')
        self._write(self.srch_text, '')


def batch_process(folder):
    xmls = []
    for dirpath, _, files in os.walk(folder):
        for fn in sorted(files):
            if fn.endswith('_dxl.xml'):
                xmls.append(os.path.join(dirpath, fn))

    if not xmls:
        print(f'No *_dxl.xml files found under: {folder}')
        return

    print(f'Found {len(xmls)} XML files under: {folder}')

    root = tk.Tk()
    root.withdraw()
    app = NSFAnalyzer(root)

    ok = 0; errs = []
    for i, xml_path in enumerate(xmls, 1):
        try:
            with open(xml_path, 'r', encoding='utf-8', errors='replace') as fh:
                raw = fh.read()
            raw = clean_xml(raw)
            root_el = ET.fromstring(raw)
            app.xml_data = app._parse_xml(root_el)
            app.xml_data['_path'] = xml_path
            app.xml_path = xml_path
            app._usage_cache  = None
            app._filter_cache = None

            report   = app._build_report_text()
            out_path = xml_path.replace('_dxl.xml', '_report.txt')
            with open(out_path, 'w', encoding='utf-8') as fh:
                fh.write(report)
            print(f'  [{i}/{len(xmls)}] OK  {os.path.relpath(out_path, folder)}')
            ok += 1
        except Exception as e:
            errs.append((xml_path, str(e)))
            print(f'  [{i}/{len(xmls)}] ERR {os.path.basename(xml_path)}: {e}')

    root.destroy()
    print(f'\nDone: {ok} reports generated, {len(errs)} errors.')
    for p, e in errs:
        print(f'  {p}: {e}')


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--batch':
        batch_process(sys.argv[2])
    else:
        root = tk.Tk()
        app = NSFAnalyzer(root)
        root.mainloop()
