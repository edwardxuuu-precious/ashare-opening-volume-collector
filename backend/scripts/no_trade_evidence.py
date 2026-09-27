"""Reviewed no-trade evidence, scoped to exact dates.

The registry deliberately does not infer a suspension from an empty quote response,
an ``ST`` name, or a notice whose effective date was not reviewed.  Rich entries
also carry a public explanation that can be shown without exposing collector
errors to the user.
"""

SSE_SUSPENSION_PAGE = 'https://www.sse.com.cn/disclosure/dealinstruc/suspension/'


def _special(status_type, label, description, started_at, source,
             announcement_title, announcement_url):
    return dict(type=status_type, label=label, description=description,
                startedAt=started_at, source=source,
                announcementTitle=announcement_title,
                announcementUrl=announcement_url)


NOTICES = {
    **{('000016', day): dict(
        noticeId='2026-55', disclosedAt='2026-08-22',
        url='https://static.cninfo.com.cn/finalpage/2026-08-22/1225490821.PDF')
       for day in ('2026-08-24','2026-08-25','2026-08-26','2026-08-27')},
    ('000016', '2026-09-07'): ('2026-69', '2026-09-04',
        'https://epaper.cs.com.cn/zgzqb/html/2026-09/04/nw.D110000zgzqb_20260904_6-B015.htm'),
    ('002731', '2026-09-07'): ('2026-109', '2026-09-07',
        'https://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2026/2026-9/2026-09-07/12586056.PDF'),
    ('002743', '2026-09-07'): ('2026-062', '2026-09-05',
        'https://epaper.cs.com.cn/zgzqb/html/2026-09/05/nw.D110000zgzqb_20260905_4-B024.htm'),
    ('002870', '2026-09-07'): ('2026-069', '2026-09-08',
        'https://static.cninfo.com.cn/finalpage/2026-09-08/1225550443.PDF'),
    ('002998', '2026-09-07'): ('2026-053', '2026-09-08',
        'https://static.cninfo.com.cn/finalpage/2026-09-08/1225551828.PDF'),
    ('301139', '2026-09-07'): ('2026-092', '2026-09-07',
        'https://static.cninfo.com.cn/finalpage/2026-09-07/1225551366.PDF'),
    ('600825', '2026-09-07'): ('2026-014', '2026-09-08',
        'https://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESH_STOCK/2026/2026-9/2026-09-08/12588319.PDF'),
    ('600929', '2026-09-07'): ('2026-034', '2026-09-05',
        'https://static.cninfo.com.cn/finalpage/2026-09-05/1225548997.PDF'),
    ('688432', '2026-09-07'): ('2026-038', '2026-09-05',
        'https://static.cninfo.com.cn/finalpage/2026-09-05/1225549389.PDF'),
    ('000016', '2026-09-18'): dict(
        noticeId='2026-69', disclosedAt='2026-09-04',
        url='https://static.cninfo.com.cn/finalpage/2026-09-01/1225537108.PDF',
        specialStatus=_special(
            'pending_delisting', '主动终止上市事项停牌',
            '公司拟以股东会决议方式主动终止A股和B股上市，股票自2026-09-04起停牌，并按主动终止上市程序继续办理后续事项。',
            '2026-09-04', '巨潮资讯公司公告',
            '关于公司股票后续停牌及现金选择权相关事宜的说明公告',
            'https://static.cninfo.com.cn/finalpage/2026-09-01/1225537108.PDF')),
    ('002731', '2026-09-18'): dict(
        noticeId='2026-105', disclosedAt='2026-08-31',
        url='https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-08-31/e4c4271e-3afc-4387-aac4-c2aeb145c32d.PDF',
        specialStatus=_special(
            'pending_delisting', '规范类退市程序停牌',
            '公司未能在规定期限内披露定期报告并触及规范类退市情形，股票自2026-09-07起继续停牌，等待深交所作出是否终止上市的决定。',
            '2026-09-07', '深圳证券交易所信息披露公司公告',
            '关于预计无法在法定期限内披露定期报告暨公司股票停牌暨可能被终止上市的风险提示公告',
            'https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-08-31/e4c4271e-3afc-4387-aac4-c2aeb145c32d.PDF')),
    ('301139', '2026-09-18'): dict(
        noticeId='1225533921', disclosedAt='2026-08-29',
        url='https://static.cninfo.com.cn/finalpage/2026-08-29/1225533921.PDF',
        specialStatus=_special(
            'pending_delisting', '重大违法退市程序停牌',
            '公司因触及重大违法强制退市情形，自2026-08-31起停牌，处于可能被终止上市的后续程序；截至目标日尚不能标记为已退市。',
            '2026-08-31', '巨潮资讯公司公告',
            '关于公司股票停牌暨可能被终止上市的风险提示公告',
            'https://static.cninfo.com.cn/finalpage/2026-08-29/1225533921.PDF')),
    ('600301', '2026-09-18'): dict(
        noticeId='SSE-LXTP-20260914', disclosedAt='2026-09-12', url=SSE_SUSPENSION_PAGE,
        specialStatus=_special(
            'control_change', '控制权变更停牌',
            '公司正在筹划控制权变更事项，股票自2026-09-14起连续停牌。',
            '2026-09-14', '上海证券交易所停复牌信息与公司公告',
            '关于筹划控制权变更暨停牌的公告', SSE_SUSPENSION_PAGE)),
    ('600825', '2026-09-18'): dict(
        noticeId='SSE-LXTP-20260908', disclosedAt='2026-09-08', url=SSE_SUSPENSION_PAGE,
        specialStatus=_special(
            'major_restructuring', '重大资产重组停牌',
            '公司正在筹划发行股份购买资产暨关联交易，股票自2026-09-08起连续停牌。',
            '2026-09-08', '上海证券交易所停复牌信息与公司公告',
            '关于筹划发行股份购买资产暨关联交易事项的停牌进展公告', SSE_SUSPENSION_PAGE)),
    ('601059', '2026-09-18'): dict(
        noticeId='SSE-LXTP-20260915', disclosedAt='2026-09-12', url=SSE_SUSPENSION_PAGE,
        specialStatus=_special(
            'merger_pending_delisting', '吸收合并停牌（拟终止上市）',
            '因中金公司换股吸收合并，股票自2026-09-15起连续停牌，拟申请主动终止上市并进入合并实施程序。',
            '2026-09-15', '上海证券交易所停复牌信息与公司公告',
            '关于公司A股股票连续停牌直至终止上市、实施换股吸收合并的提示性公告', SSE_SUSPENSION_PAGE)),
    ('601198', '2026-09-18'): dict(
        noticeId='SSE-LXTP-20260915', disclosedAt='2026-09-12', url=SSE_SUSPENSION_PAGE,
        specialStatus=_special(
            'merger_pending_delisting', '吸收合并停牌（拟终止上市）',
            '因中金公司换股吸收合并，股票自2026-09-15起连续停牌，拟申请主动终止上市并进入合并实施程序。',
            '2026-09-15', '上海证券交易所停复牌信息与公司公告',
            '关于公司A股股票连续停牌直至终止上市、实施换股吸收合并的提示性公告', SSE_SUSPENSION_PAGE)),
    ('601238', '2026-09-18'): dict(
        noticeId='SSE-LXTP-20260915', disclosedAt='2026-09-15', url=SSE_SUSPENSION_PAGE,
        specialStatus=_special(
            'major_restructuring', '重大资产重组停牌',
            '公司正在筹划重大资产重组事项，股票自2026-09-15起连续停牌。',
            '2026-09-15', '上海证券交易所停复牌信息与公司公告',
            '关于筹划重大资产重组事项的停牌公告', SSE_SUSPENSION_PAGE)),
    ('601995', '2026-09-18'): dict(
        noticeId='SSE-LXTP-20260915', disclosedAt='2026-09-08', url=SSE_SUSPENSION_PAGE,
        specialStatus=_special(
            'merger', '换股吸收合并停牌',
            '公司因实施换股吸收合并东兴证券、信达证券，A股股票自2026-09-15起停牌。',
            '2026-09-15', '上海证券交易所停复牌信息与公司公告',
            '关于公司A股股票停牌的提示性公告', SSE_SUSPENSION_PAGE)),
    ('603400', '2026-09-18'): dict(
        noticeId='SSE-LXTP-20260915', disclosedAt='2026-09-15', url=SSE_SUSPENSION_PAGE,
        specialStatus=_special(
            'major_restructuring', '重大资产重组停牌',
            '公司正在筹划发行股份及支付现金购买资产并募集配套资金，股票自2026-09-15起停牌。',
            '2026-09-15', '上海证券交易所停复牌信息与公司公告',
            '关于筹划发行股份及支付现金方式购买资产并募集配套资金事项的停牌公告', SSE_SUSPENSION_PAGE)),
    ('605303', '2026-09-18'): dict(
        noticeId='SSE-LXTP-20260914', disclosedAt='2026-09-12', url=SSE_SUSPENSION_PAGE,
        specialStatus=_special(
            'major_restructuring', '重大资产重组停牌',
            '公司正在筹划发行股份及支付现金购买资产，股票自2026-09-14起连续停牌。',
            '2026-09-14', '上海证券交易所停复牌信息与公司公告',
            '关于筹划发行股份及支付现金购买资产的停牌公告', SSE_SUSPENSION_PAGE)),
    ('688496', '2026-09-18'): dict(
        noticeId='2026-09-11-EUPJ', disclosedAt='2026-09-11',
        url='https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-09-11/688496_20260911_EUPJ.pdf',
        specialStatus=_special(
            'pending_delisting', '交易类退市程序停牌',
            '公司股票触及交易类强制退市情形，自2026-09-11起停牌，并已收到上交所终止上市事先告知；截至目标日尚不能标记为已退市。',
            '2026-09-11', '上海证券交易所公司公告',
            '关于公司股票触及交易类强制退市的风险提示暨停牌公告',
            'https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-09-11/688496_20260911_EUPJ.pdf')),
}


def evidence(code, day):
    notice = NOTICES.get((code, day))
    if not notice: return None
    if isinstance(notice, dict):
        number, disclosed, url = notice['noticeId'], notice['disclosedAt'], notice['url']
        special_status = notice.get('specialStatus')
    else:
        number, disclosed, url = notice
        special_status = None
    result = dict(kind='company_suspension_notice', code=code, date=day, noticeId=number,
                disclosedAt=disclosed, url=url, validatedDates=[day], reviewedAt='2026-09-08',
                evidenceTiming=('retrospective_confirmation' if disclosed > day else
                                'ongoing_suspension_notice' if code == '600929' else 'contemporaneous'))
    if special_status:
        result['reviewedAt'] = '2026-09-20'
        result['specialStatus'] = special_status
    return result


# Explanations are separately reviewed presentation data. Adding one must not
# change the identity of a previously published exact-date no-trade proof.
REVIEWED_EXPLANATIONS = {
    ('000016', '2026-09-07'): _special(
        'pending_delisting', '主动终止上市事项停牌',
        '公司拟以股东会决议方式主动终止上市，股票自2026-09-04起停牌；截至目标日该事项尚待股东会审议，不能标记为已退市。',
        '2026-09-04', '巨潮资讯公司公告',
        '关于本次终止上市事项暨距离公司股票停牌尚余3个交易日的提示性公告',
        'https://static.cninfo.com.cn/finalpage/2026-09-01/1225537108.PDF'),
    ('002731', '2026-09-07'): _special(
        'pending_delisting', '规范类退市程序停牌',
        '公司未按期披露定期报告，股票自2026-09-01起停牌，并自2026-09-07起因规范类退市情形继续停牌，等待交易所作出后续决定。',
        '2026-09-01', '公司公告及深圳证券交易所信息披露',
        '关于公司股票将被终止上市暨继续停牌的风险提示公告',
        'https://file.finance.sina.com.cn/211.154.219.97:9494/MRGG/CNSESZ_STOCK/2026/2026-9/2026-09-07/12586056.PDF'),
    ('002743', '2026-09-07'): _special(
        'risk_warning_transition', '风险警示切换停牌',
        '公司主要银行账户被冻结，公告安排股票于2026-09-07停牌一天，9月8日起复牌并实施其他风险警示；这不是持续多日停牌。',
        '2026-09-07', '公司指定信息披露媒体：中国证券报',
        '关于公司主要银行账号被冻结暨股票交易被实施其他风险警示及股票停复牌的公告',
        'https://epaper.cs.com.cn/zgzqb/html/2026-09/05/nw.D110000zgzqb_20260905_4-B024.htm'),
    ('002870', '2026-09-07'): _special(
        'asset_acquisition', '筹划资产收购停牌',
        '公司筹划发行股份及支付现金购买资产并募集配套资金，股票自2026-09-01起停牌；公告明确本次交易预计不构成重大资产重组，目标日仍在停牌。',
        '2026-09-01', '巨潮资讯公司公告',
        '关于筹划发行股份及支付现金方式购买资产并募集配套资金事项的停牌进展公告',
        'https://static.cninfo.com.cn/finalpage/2026-09-08/1225550443.PDF'),
    ('002998', '2026-09-07'): _special(
        'control_change', '控制权变更停牌',
        '实际控制人正在筹划公司控制权变更，股票自2026-09-04起停牌；9月8日公告追溯确认原停牌安排，并说明将继续停牌。',
        '2026-09-04', '巨潮资讯公司公告',
        '关于筹划公司控制权变更事项进展暨继续停牌的公告',
        'https://static.cninfo.com.cn/finalpage/2026-09-08/1225551828.PDF'),
    ('301139', '2026-09-07'): _special(
        'pending_delisting', '重大违法退市程序停牌',
        '公司因证券发行文件虚假记载触及重大违法强制退市情形，交易所拟终止其股票上市，股票自2026-08-31起停牌；目标日尚不能标记为已完成退市。',
        '2026-08-31', '巨潮资讯公司公告',
        '关于公司股票被实施其他风险警示的第七次提示性公告',
        'https://static.cninfo.com.cn/finalpage/2026-09-07/1225551366.PDF'),
    ('600825', '2026-09-07'): _special(
        'major_restructuring', '重大资产重组停牌',
        '公司筹划发行股份购买资产暨关联交易，公告正文确认股票已于2026-09-07开市起停牌，并于9月8日起继续停牌。',
        '2026-09-07', '巨潮资讯公司公告',
        '关于筹划发行股份购买资产暨关联交易事项的停牌公告',
        'https://static.cninfo.com.cn/finalpage/2026-09-08/1225552231.PDF'),
    ('600929', '2026-09-07'): _special(
        'major_restructuring', '重大资产重组停牌',
        '公司筹划发行股份及支付现金购买资产并募集配套资金，预计构成重大资产重组，股票自2026-08-31起停牌；目标日仍处于原公告的停牌期间。',
        '2026-08-31', '巨潮资讯公司公告',
        '关于筹划重大资产重组的停牌进展公告',
        'https://static.cninfo.com.cn/finalpage/2026-09-05/1225548997.PDF'),
    ('688432', '2026-09-07'): _special(
        'asset_acquisition', '筹划资产收购停牌',
        '公司筹划发行股份及支付现金购买资产并募集配套资金暨关联交易，股票自2026-08-31起停牌；公告明确9月7日起继续停牌。',
        '2026-08-31', '巨潮资讯公司公告',
        '关于筹划发行股份及支付现金购买资产并募集配套资金暨关联交易事项的进展并继续停牌的公告',
        'https://static.cninfo.com.cn/finalpage/2026-09-05/1225549389.PDF'),
}


def explanation(code, day):
    value = REVIEWED_EXPLANATIONS.get((code, day))
    return dict(value) if value else None


def valid_notice(value, code):
    return isinstance(value, dict) and value.get('code') == code and value == evidence(code, value.get('date'))
