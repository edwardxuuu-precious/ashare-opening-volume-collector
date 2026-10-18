"""Bounded chart transport; never used by the model or screening calculations."""
import hashlib
import json
import base64
import gzip
from decimal import Decimal,localcontext

MAX_DISPLAY_BINS=4096


def response_upper_bound(value):
    """Match the existing auth handler's browser gzip encoding, with headroom."""
    body=json.dumps(value,ensure_ascii=False).encode()
    if len(body)<65536:return len(body)*6+20000
    return len(base64.b64encode(gzip.compress(body,compresslevel=6,mtime=0)))+20000


def display_frame(value):
    snapshots=value.get('snapshots',{})
    if not any(len(s.get('profile',[]))>MAX_DISPLAY_BINS for s in snapshots.values()):
        return value
    # Keep exact shares at every price used by either date's peak comparison.
    anchors={p for s in snapshots.values() for p in s['peak_prices_cny']}
    result={**value,'snapshots':{}}
    with localcontext() as context:
        context.prec=60
        for day,snapshot in snapshots.items():
            profile=snapshot['profile']
            if len(profile)<=MAX_DISPLAY_BINS:
                result['snapshots'][day]=snapshot
                continue
            if snapshot.get('profile_format')=='DISPLAY_BANDS_WITH_EXACT_ANCHORS':
                raise ValueError('DISPLAY_PROFILE_CANNOT_BE_REAGGREGATED_AS_EXACT_INPUT')
            first=int(Decimal(profile[0]['price_cny'])*100)
            last=int(Decimal(profile[-1]['price_cny'])*100)
            width=max(1,(last-first+1+MAX_DISPLAY_BINS-1)//MAX_DISPLAY_BINS)
            groups={};exact={p:'0' for p in anchors}
            for point in profile:
                price=point['price_cny'];cent=int(Decimal(price)*100)
                if price in exact:exact[price]=point['share_percent']
                index=(cent-first)//width
                group=groups.setdefault(index,{'low':cent,'high':cent,'mass':Decimal(0)})
                group['high']=cent;group['mass']+=Decimal(point['share_percent'])
            display=[{'price_cny':str(Decimal(g['low']+g['high'])/200),'share_percent':str(g['mass'])}
                for _,g in sorted(groups.items())]
            result['snapshots'][day]={**snapshot,'profile':display,
                'profile_format':'DISPLAY_BANDS_WITH_EXACT_ANCHORS',
                'display_bin_width_cny':str(Decimal(width)/100),
                'exact_anchor_shares_percent':exact,'original_profile_bin_count':len(profile),
                'original_profile_sha256':hashlib.sha256(json.dumps(profile,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()}
    return result
