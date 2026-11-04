#!/usr/bin/env python3
"""Regenerate NFQ focus positions/visibility from the current mod's native focus trees.

Run from any directory with Python 3:
    python tools/generate_nfq_layout.py

This updates only NFQ-generated position, branch-visibility, scripted-trigger and mutually-
exclusive cleanup files. It does not copy or delete the mod folder.
"""
from collections import defaultdict
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {
    "GER": ("germany.txt", 539), "ENG": ("uk.txt", 268),
    "USA": ("usa.txt", 236), "SOV": ("soviet.txt", 412),
    "JAP": ("japan.txt", 549), "ITA": ("italy.txt", 415),
    "FRA": ("france.txt", 286), "RAJ": ("india_goe.txt", 245),
}
TOKEN = re.compile(r'''\s+|\#[^\n]*|("(?:\\.|[^"\\])*")|([{}=])|([^\s{}=#]+)''')

def parse(text):
    tokens=[]
    for m in TOKEN.finditer(text):
        if m.group(0).isspace() or m.group(0).startswith('#'):
            continue
        tokens.append(m.group(1) or m.group(2) or m.group(3))
    i=0
    def block(until=False):
        nonlocal i
        out=[]
        while i < len(tokens):
            if tokens[i] == '}':
                if not until: raise ValueError('unexpected closing brace')
                i += 1
                return out
            key=tokens[i]; i+=1
            if i >= len(tokens) or tokens[i] != '=':
                out.append((key, True)); continue
            i+=1
            if i < len(tokens) and tokens[i] == '{':
                i+=1; val=block(True)
            elif i < len(tokens):
                val=tokens[i]; i+=1
            else:
                val=None
            out.append((key,val))
        if until: raise ValueError('missing closing brace')
        return out
    return block()

def values(node,key): return [v for k,v in node if k==key]
def first(node,key,default=None):
    found=values(node,key)
    return found[0] if found else default

def unquote(v):
    return v[1:-1] if isinstance(v,str) and len(v)>=2 and v[0]=='"' and v[-1]=='"' else v

def serialize(node,indent=0):
    pad=' '*indent; out=[]
    for key,val in node:
        if val is True: out.append(f'{pad}{key}')
        elif isinstance(val,list):
            out.append(f'{pad}{key} = {{'); out.extend(serialize(val,indent+4).splitlines()); out.append(f'{pad}}}')
        else: out.append(f'{pad}{key} = {val}')
    return '\n'.join(out)

def replace_block(text,name,newblock):
    pattern=re.compile(r'(?m)^\s*'+re.escape(name)+r'\s*=\s*\{')
    m=pattern.search(text)
    if not m: raise RuntimeError(f'Could not find effect {name}')
    opening=text.find('{',m.start(),m.end())
    depth=0; quoted=False; escaped=False; comment=False
    for idx in range(opening,len(text)):
        c=text[idx]
        if comment:
            if c=='\n': comment=False
        elif quoted:
            if escaped: escaped=False
            elif c=='\\': escaped=True
            elif c=='"': quoted=False
        else:
            if c=='#': comment=True
            elif c=='"': quoted=True
            elif c=='{': depth+=1
            elif c=='}':
                depth-=1
                if depth==0:
                    return text[:m.start()]+newblock+text[idx+1:]
    raise RuntimeError(f'Unclosed effect {name}')

# Read each focus tree and its ID-to-focus mapping.
trees={}; foci={}; ids={}; mapping={}; positions={}; chains={}
for tag,(file_name,_) in CONFIG.items():
    parsed=parse((ROOT/'common/national_focus'/file_name).read_text(encoding='utf-8-sig'))
    focus_tree=values(parsed,'focus_tree')[0]
    tree_id={unquote(first(f,'id')):f for k,f in focus_tree if k=='focus' and isinstance(f,list) and first(f,'id')}
    trees[tag]=focus_tree; foci[tag]=tree_id
    tag_map={}
    for path in (ROOT/'history/units'/tag).glob('*.txt'):
        match=re.fullmatch(re.escape(tag)+r'_(\d+)',path.stem)
        if not match: continue
        content=path.read_text(encoding='utf-8-sig',errors='ignore')
        target=re.search(r'\bcurrent\s*=\s*([\w.]+)',content)
        if target: tag_map[int(match.group(1))]=target.group(1)
    missing={n:fid for n,fid in tag_map.items() if fid not in tree_id}
    if missing: raise RuntimeError(f'{tag}: mapping targets not in focus tree: {list(missing.items())[:5]}')
    mapping[tag]=tag_map
    pos_cache={}; chain_cache={}; active=set()
    def get_chain(fid):
        if fid in chain_cache: return chain_cache[fid]
        if fid in active: raise RuntimeError(f'{tag}: position ancestry cycle at {fid}')
        active.add(fid)
        focus=tree_id[fid]
        parent=unquote(first(focus,'relative_position_id'))
        result=(get_chain(parent) if parent in tree_id else [])+[fid]
        active.remove(fid); chain_cache[fid]=result
        return result
    def get_position(fid):
        if fid in pos_cache: return pos_cache[fid]
        focus=tree_id[fid]
        x=int(float(unquote(first(focus,'x','0')))); y=int(float(unquote(first(focus,'y','0'))))
        parent=unquote(first(focus,'relative_position_id'))
        if parent in tree_id:
            px,py=get_position(parent); x+=px; y+=py
        pos_cache[fid]=(x,y); return x,y
    for fid in tree_id:
        get_chain(fid); get_position(fid)
    positions[tag]=pos_cache; chains[tag]=chain_cache

# German advisor/event-only nodes use this native text icon and cannot be started by NFQ.
# The Todt-Wehrmacht integration capstone keeps the inner-circle icon but is a normal,
# player-selectable focus, so it must remain queueable in NFQ.
character_only_foci = {
    "GER": {fid for fid, focus in foci["GER"].items()
            if unquote(first(focus, 'text_icon')) == "GER_inner_circle_focus_style"
            and fid != "GER_integrate_organization_todt_into_wehrmacht"}
}
character_only_numbers = {
    tag: {number for number, fid in mapping[tag].items()
          if tag == "GER" and fid in character_only_foci["GER"]}
    for tag in CONFIG
}
# Keep the guard trigger generated from the live focus tree so future tree edits do not drift.
char_trigger = [
    '# Generated by tools/generate_nfq_layout.py. Do not edit by hand.',
    'NFQ_character_only_focus = {', '    AND = {', '        tag = GER', '        OR = {'
]
for number in sorted(character_only_numbers["GER"]):
    char_trigger.append(f'            check_variable = {{ n = {number} }}')
char_trigger.extend(['        }', '    }', '}', ''])
(ROOT/'common/scripted_triggers/NFQ_character_only_foci.txt').write_text('\n'.join(char_trigger), encoding='utf-8')

# Generate recursive visibility triggers from native focus prerequisites, allow_branch, and symmetric exclusives.
for tag,focuses in foci.items():
    exclusions=defaultdict(set)
    id_by_focus={fid:number for number,fid in mapping[tag].items()}
    for fid,focus in focuses.items():
        for block in values(focus,'mutually_exclusive'):
            for other in values(block,'focus'):
                other=unquote(other)
                if isinstance(other,str): exclusions[fid].add(other); exclusions[other].add(fid)
    lines=['# Generated file. Regenerate with tools/generate_nfq_layout.py.']
    for fid,focus in focuses.items():
        # This focus sits after several character/event-only vanilla focuses.
        # Those prerequisite nodes are intentionally excluded from NFQ, so deriving
        # its ball visibility from their NFQ visibility hides this branch forever.
        # The focus itself is visible in the native tree from game start; NFQ must
        # therefore show its ball, and let NFQ's normal queue/completion rules work.
        if tag == 'GER' and fid == 'GER_loyalty_to_the_fuhrer':
            lines.append(f'NFQ_focus_visible_{tag}_{fid} = {{')
            lines.append('    always = yes')
            lines.extend(['}', ''])
            continue
        # The Todt-Wehrmacht integration focus follows an advisor-only focus, so it
        # must derive its visibility from the completed autonomous focus instead of
        # recursing into that focus's intentionally hidden NFQ visibility.
        if tag == 'GER' and fid == 'GER_integrate_organization_todt_into_wehrmacht':
            lines.append(f'NFQ_focus_visible_{tag}_{fid} = {{')
            lines.append('    OR = {')
            lines.append('        has_completed_focus = GER_autonomous_organization_todt')
            lines.append('        check_variable = { f288 = 5 }')
            lines.append('    }')
            lines.extend(['}', ''])
            continue
        if tag == 'GER' and fid in character_only_foci['GER']:
            lines.append(f'NFQ_focus_visible_{tag}_{fid} = {{')
            lines.append('    always = no')
            lines.extend(['}', ''])
            continue
        conditions=[]
        # Mirror native focus visibility conditions as well as branch structure.
        # Do not mirror `available`: NFQ deliberately allows queuing future focuses
        # whose completion prerequisites are not satisfied yet.
        for visible in values(focus,'visible'):
            if isinstance(visible,list): conditions.append('AND = {\n'+serialize(visible,4)+'\n}')
        for allow in values(focus,'allow_branch'):
            if isinstance(allow,list): conditions.append('AND = {\n'+serialize(allow,4)+'\n}')
        alternatives=sorted(exclusions.get(fid,set()))
        if alternatives:
            # A focus completion reward can run before the engine sets has_completed_focus.
            # The NFQ completion hook sets f<ID>=5 immediately, so check both sources.
            alternative_conditions=[]
            for alt in alternatives:
                checks=[f'has_completed_focus = {alt}']
                if alt in id_by_focus:
                    checks.append(f'check_variable = {{ f{id_by_focus[alt]} = 5 }}')
                if len(checks)==1:
                    alternative_conditions.append(checks[0])
                else:
                    alternative_conditions.append('OR = {\n'+''.join(f'    {c}\n' for c in checks)+'}')
            if len(alternative_conditions)==1:
                conditions.append('NOT = {\n'+''.join('    '+line+'\n' for line in alternative_conditions[0].splitlines())+'}')
            else:
                conditions.append('NOT = {\n    OR = {\n'+''.join('        '+line+'\n' for c in alternative_conditions for line in c.splitlines())+'    }\n}')
        reqs=values(focus,'prerequisite')
        if reqs:
            groups=[]
            for req in reqs:
                if not isinstance(req,list): continue
                parents=[unquote(val) for key,val in req if key=='focus']
                extra=[(key,val) for key,val in req if key!='focus']
                required=[f'NFQ_focus_visible_{tag}_{p} = yes' if p in focuses else 'always = yes' for p in parents]
                if not required and not extra: continue
                # In HOI4, several focus IDs inside one prerequisite block are OR.
                if len(required)>1:
                    group='OR = {\n'+'\n'.join('    '+r for r in required)+'\n}'
                elif required:
                    group=required[0]
                else:
                    group='always = yes'
                if extra:
                    group='AND = {\n    '+group.replace('\n','\n    ')+'\n'+serialize(extra,4)+'\n}'
                groups.append(group)
            # Several prerequisite blocks on the same focus are AND.
            if groups:
                conditions.append(groups[0] if len(groups)==1 else 'AND = {\n'+'\n'.join('    '+g.replace('\n','\n    ') for g in groups)+'\n}')
        lines.append(f'NFQ_focus_visible_{tag}_{fid} = {{')
        if conditions:
            lines.append('    AND = {')
            for c in conditions: lines.extend('        '+line for line in c.splitlines())
            lines.append('    }')
        else: lines.append('    always = yes')
        lines.extend(['}',''])
    (ROOT/'common/scripted_triggers'/f'NFQ_autolayout_{tag}.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')

# Generate base positions plus native offset blocks through the relative-position ancestry chain.
for tag,(file_name,last_id) in CONFIG.items():
    reset=[f'NFQ_focus_tree_reset_position_{tag} = {{']
    for number,fid in sorted(mapping[tag].items()):
        x,y=positions[tag][fid]
        # The overlay button is 30x30. Anchor it to the top-right corner of
        # the corresponding native focus tile instead of the tile's top-left.
        reset.extend([f'    set_variable = {{ x{number} = {x*96} }}',f'    set_variable = {{ y{number} = {y*130} }}'])
        for ancestor in chains[tag][fid]:
            for offset in values(foci[tag][ancestor],'offset'):
                if not isinstance(offset,list): continue
                ox=int(float(unquote(first(offset,'x','0')))); oy=int(float(unquote(first(offset,'y','0'))))
                if ox==0 and oy==0: continue
                trigger=first(offset,'trigger')
                if isinstance(trigger,list):
                    reset.extend(['    if = {','        limit = {'])
                    reset.extend('            '+line for line in serialize(trigger).splitlines())
                    reset.append('        }')
                    if ox: reset.append(f'        add_to_variable = {{ x{number} = {ox*96} }}')
                    if oy: reset.append(f'        add_to_variable = {{ y{number} = {oy*130} }}')
                    reset.append('    }')
                else:
                    if ox: reset.append(f'    add_to_variable = {{ x{number} = {ox*96} }}')
                    if oy: reset.append(f'    add_to_variable = {{ y{number} = {oy*130} }}')
    reset.append('}')
    update=f'NFQ_focus_tree_update_position_{tag} = {{\n    # All layout offsets are calculated in reset_position from the native focus data.\n}}'
    active_ids=set(mapping[tag])
    branch=[f'NFQ_focus_tree_allow_branch_{tag} = {{']
    for number in range(101,550):
        if number not in active_ids:
            branch.extend([f'    set_variable = {{ f{number} = 8 }}',f'    set_variable = {{ t{number} = 0 }}'])
    for number,fid in sorted(mapping[tag].items()):
        branch.extend([
            '    if = {',
            f'        limit = {{ NFQ_focus_visible_{tag}_{fid} = yes }}',
            '        if = {',
            f'            limit = {{ OR = {{ check_variable = {{ f{number} = 6 }} check_variable = {{ f{number} = 8 }} }} }}',
            f'            set_variable = {{ f{number} = 1 }}',
            '        }',
            '    }',
            '    else = {',
            f'        if = {{ limit = {{ check_variable = {{ f{number} < 4 }} }}',
            f'            if = {{ limit = {{ is_in_array = {{ array = NFQ_focus_queue value = {number} }} }} remove_from_array = {{ NFQ_focus_queue = {number} }} }}',
            f'            set_variable = {{ f{number} = 8 }}',
            f'            set_variable = {{ t{number} = 0 }}',
            '        }',
            '    }',
        ])
    if tag == 'GER':
        branch.append('    # NFQ: purge character-only inner-circle nodes from existing queues.')
        for number in sorted(character_only_numbers['GER']):
            branch.append(f'    if = {{ limit = {{ is_in_array = {{ array = NFQ_focus_queue value = {number} }} }} remove_from_array = {{ NFQ_focus_queue = {number} }} }}')
            branch.append(f'    set_variable = {{ f{number} = 8 }}')
            branch.append(f'    set_variable = {{ t{number} = 0 }}')
    branch.extend(['    NFQ_sync_tree_to_queue = yes','}'])
    path=ROOT/'common/scripted_effects'/f'NFQ_scr_eff_{tag}.txt'
    text=path.read_text(encoding='utf-8-sig')
    text=replace_block(text,f'NFQ_focus_tree_reset_position_{tag}','\n'.join(reset))
    text=replace_block(text,f'NFQ_focus_tree_update_position_{tag}',update)
    text=replace_block(text,f'NFQ_focus_tree_allow_branch_{tag}','\n'.join(branch))
    text=re.sub(r'(?m)^\s*NFQ_slide_xpos\s*=\s*yes\s*$', '# NFQ_slide_xpos disabled to keep the overlay aligned with native focus coordinates.',text)
    path.write_text(text,encoding='utf-8')

# Regenerate mutual-exclusion cleanup based on the current focus tree definitions.
exclusive=['# Generated mutual-exclusion cleanup. Hidden alternative focus balls are assigned f=8.']
for tag,focuses in foci.items():
    id_by_focus={fid:number for number,fid in mapping[tag].items()}
    pairs=defaultdict(set)
    for fid,focus in focuses.items():
        for block in values(focus,'mutually_exclusive'):
            for other in values(block,'focus'):
                other=unquote(other); pairs[fid].add(other); pairs[other].add(fid)
    for number,fid in sorted((n,f) for n,f in mapping[tag].items()):
        losers=sorted({id_by_focus[o] for o in pairs.get(fid,set()) if o in id_by_focus and id_by_focus[o]!=number})
        exclusive.append(f'NFQ_cancel_exclusive_{tag}_{number} = {{')
        for loser in losers:
            exclusive.append(f'    if = {{ limit = {{ is_in_array = {{ array = NFQ_focus_queue value = {loser} }} }} remove_from_array = {{ NFQ_focus_queue = {loser} }} }}')
            exclusive.append(f'    set_variable = {{ f{loser} = 8 }}')
            exclusive.append(f'    set_variable = {{ t{loser} = 0 }}')
        exclusive.extend(['    NFQ_set_text_variables = yes','    NFQ_dirty = yes','}',''])
(ROOT/'common/scripted_effects/NFQ_scr_eff_exclusive.txt').write_text('\n'.join(exclusive)+'\n',encoding='utf-8')

# The shared GUI reuses numeric IDs across countries. Attach the generated x/y
# variables to every overlay ball and queue counter; without these properties the
# script calculates coordinates but the GUI controls remain at their default position.
gui_path = ROOT/'common/scripted_guis/NFQ_scr_guis_focustree.txt'
gui_text = gui_path.read_text(encoding='utf-8-sig')
properties = ['properties={']
for number in range(101, 550):
    properties.append(f'{number}={{frame=f{number} x=x{number} y=y{number}}}')
    properties.append(f'{number}t={{ x=x{number} y=y{number} }}')
properties.append('}')
properties_block = '\n'.join(properties)
if re.search(r'(?m)^\s*properties\s*=\s*\{', gui_text):
    gui_text = replace_block(gui_text, 'properties', properties_block)
else:
    if 'effects={' not in gui_text:
        raise RuntimeError('Could not find effects block in NFQ scripted GUI')
    gui_text = gui_text.replace('effects={', properties_block+'\neffects={', 1)
for number in sorted(character_only_numbers['GER']):
    old = f'{number}_visible={{check_variable={{f{number}<4}}}}{number}t_visible={{check_variable={{t{number}>0}}}}'
    new = f'{number}_visible={{AND={{NOT={{tag=GER}}check_variable={{f{number}<4}}}}}}{number}t_visible={{AND={{NOT={{tag=GER}}check_variable={{t{number}>0}}}}}}'
    if old in gui_text:
        gui_text = gui_text.replace(old, new, 1)
    elif new not in gui_text:
        raise RuntimeError(f'Cannot find NFQ GUI visibility entry for excluded focus ID {number}')
gui_path.write_text(gui_text, encoding='utf-8')

# GUI parser is sensitive to a UTF-8 BOM before guiTypes; remove it on regeneration.
interface_gui = ROOT/'interface/NFQ_focustree.gui'
if interface_gui.exists():
    interface_text = interface_gui.read_text(encoding='utf-8-sig')
    if not re.search(r'name\s*=\s*"549"', interface_text):
        widget = '''\t\tbuttonType = {\n\t\t\tname = \"549\"\n\t\t\tspriteType = \"GFX_icon\"\n\t\t}\n\t\tinstantTextboxType = {\n\t\t\tname = \"549t\"\n\t\t\ttext = \"[?t549]\"\n\t\t\tfont = \"hoi_16mbs\"\n\t\t\tformat = center\n\t\t\tbordersize = { x = 0 y = 9 }\n\t\t\tmaxWidth = 30\n\t\t}\n'''
        close = interface_text.rfind('\n\t}\n}')
        if close < 0:
            raise RuntimeError('Cannot find closing container in NFQ_focustree.gui')
        interface_text = interface_text[:close] + '\n' + widget + interface_text[close:]
    interface_gui.write_text(interface_text, encoding='utf-8')

print('NFQ layout generated successfully for:', ', '.join(CONFIG))
print('German character-only NFQ focuses excluded:', len(character_only_numbers['GER']))
