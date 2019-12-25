import re
from collections import defaultdict
from sefaria.model import *
import logging
logger = logging.getLogger(__name__)


def get_topics(topic, with_links, annotate_links, with_refs, group_related):
    topic_obj = Topic().load({"slug": topic})
    response = topic_obj.contents()
    response['primaryTitle'] = {
        'en': topic_obj.get_primary_title('en'),
        'he': topic_obj.get_primary_title('he')
    }
    intra_link_query = {"$or": [{"fromTopic": topic}, {"toTopic": topic}]}
    if with_links and with_refs:
        # can load faster by querying `topic_links` query just once
        all_links = TopicLinkSetHelper.find(intra_link_query)
        intra_links = [l.contents() for l in all_links if isinstance(l, IntraTopicLink)]
        response['refs'] = [l.contents() for l in all_links if isinstance(l, RefTopicLink)]
    else:
        if with_links:
            intra_links = [l.contents() for l in IntraTopicLinkSet(intra_link_query)]
        if with_refs:
            response['refs'] = [l.contents() for l in RefTopicLinkSet({"toTopic": topic})]
    if with_links:
        response['links'] = {}
        from_topic_set = set()  # duplicates can crop up for symmetric edges b/c of $or query
        for link in intra_links:
            is_inverse = link['toTopic'] == topic
            other_topic_slug = link['fromTopic'] if is_inverse else link['toTopic']
            if other_topic_slug in from_topic_set:
                continue
            from_topic_set.add(other_topic_slug)
            del link['toTopic']
            del link['class']
            link['fromTopic'] = other_topic_slug
            link_type = library.get_link_type(link['linkType'])
            del link['linkType']
            if link_type.get('groupRelated', is_inverse, False):
                link_type_slug = TopicLinkType.related_type
            else:
                link_type_slug = link_type.get('slug', is_inverse)
            link['isInverse'] = is_inverse
            if annotate_links:
                # add display information
                # TODO load all-at-once with TopicSet
                other_topic = Topic().load({"slug": other_topic_slug})
                if other_topic is None:
                    logger.warning("Topic slug {} doesn't exist. Linked to {}".format(other_topic_slug, topic))
                    continue
                link["fromTopicTitle"] = {
                    "en": other_topic.get_primary_title('en'),
                    "he": other_topic.get_primary_title('he')
                }
            if link_type_slug in response['links']:
                response['links'][link_type_slug]['links'] += [link]
            else:
                response['links'][link_type_slug] = {
                    'links': [link],
                    'title': link_type.get('displayName', is_inverse),
                    'shouldDisplay': link_type.get('shouldDisplay', is_inverse, False)
                }
                if link_type.get('pluralDisplayName', is_inverse, False):
                    response['links'][link_type_slug]['pluralTitle'] = link_type.get('pluralDisplayName', is_inverse)
    return response


def generate_topic_links_from_sheets():
    """
    Processes all public source sheets to create topic links.
    """
    from sefaria.system.database import db
    from sefaria.recommendation_engine import RecommendationEngine
    from tqdm import tqdm

    RefTopicLinkSet({"generatedBy": "sheet-topic-aggregator"}).delete()
    all_topics = {}
    results = []
    query = {"status": "public", "viaOwner": {"$exists": 0}, "assignment_id": {"$exists": 0}}
    projection = {"topics": 1, "includedRefs": 1, "owner": 1}
    # ignore sheets that are copies or were assignments
    sheet_list = db.sheets.find(query, projection)
    for sheet in tqdm(sheet_list, desc="aggregating sheet topics"):
        sheet_topics = sheet.get("topics", [])
        for topic_dict in sheet_topics:
            slug = topic_dict['slug']
            if slug not in all_topics:
                all_topics[slug] = {
                                "topic": slug,
                                "sources_dict": defaultdict(set),
                                "related_topics_dict": defaultdict(set)
                            }
            for tref in sheet.get("includedRefs", []):
                try:
                    oref = Ref(tref)
                    for sub_oref in oref.range_list():
                        all_topics[slug]["sources_dict"][sub_oref.normal()].add(sheet['owner'])
                except:
                    continue
            for related_topic_dict in sheet_topics:
                if slug != related_topic_dict['slug']:
                    all_topics[slug]["related_topics_dict"][related_topic_dict['slug']].add(sheet['owner'])

    for slug, blob in tqdm(all_topics.items(), desc="creating sheet topic links"):
        # filter related topics with less than 2 users who voted for it
        related_topics = [related_topic for related_topic in blob['related_topics_dict'].items() if len(related_topic[1]) >= 2]
        for related_topic, user_votes in related_topics:
            tl = IntraTopicLink({
                "class": "intraTopic",
                "fromTopic": related_topic,
                "toTopic": slug,
                "linkType": "sheets-related-to",
                "dataSource": "sefaria-users",
                "generatedBy": "sheet-topic-aggregator",
                "order": {"user_votes": len(user_votes)}
            })
            tl.save()
        # filter sources with less than 2 users who added it
        sources = [source for source in blob['sources_dict'].items() if len(source[1]) >= 2]

        # transform data to more convenient format
        temp_sources = []
        for source in sources:
            temp_sources += [(Ref(source[0]), len(source[1]))]
        sources = temp_sources

        # cluster refs that are close to each other
        temp_sources = []
        if len(sources) == 0:
            continue
        refs, counts = zip(*sources)
        clustered = RecommendationEngine.cluster_close_refs(refs, counts, 2)
        for cluster in clustered:
            ranged_ref = cluster[0]['ref'].to(cluster[-1]['ref'])
            counts = [x['data'] for x in cluster]
            avg_count = sum(counts) / len(cluster)
            max_count = max(counts)
            if max_count >= 3:
                temp_sources += [(ranged_ref.normal(), [r.normal() for r in ranged_ref.range_list()], avg_count)]
            # else:
            #     print("Rejected!", max_count, slug, ranged_ref.normal())
        sources = temp_sources

        # create links
        for source in sources:
            rtl = RefTopicLink({
                "class": "refTopic",
                "toTopic": slug,
                "ref": source[0],
                "expandedRefs": source[1],
                "linkType": "about",
                "is_sheet": False,
                "dataSource": "sefaria-users",
                "generatedBy": "sheet-topic-aggregator",
                "order": {"user_votes": source[2]}
            })
            rtl.save()
        # related_topics = sorted(iter(related_topics_dict.items()), key=lambda k_v1: k_v1[1], reverse=True)
        # related_topics = [topic for topic in related_topics if topic[0] in topics]


def tokenize_words_for_tfidf(text, stopwords):
    from sefaria.utils.hebrew import strip_cantillation

    try:
        text = TextChunk._strip_itags(text)
    except AttributeError:
        pass
    text = strip_cantillation(text, strip_vowels=True)
    text = re.sub(r'<[^>]+>', ' ', text)
    for match in re.finditer(r'\(.*?\)', text):
        if len(match.group().split()) <= 5:
            text = text.replace(match.group(), " ")
    text = re.sub(r'־', ' ', text)
    text = re.sub(r'\[[^\[\]]{1,7}\]', '',
                  text)  # remove kri but dont remove too much to avoid messing with brackets in talmud
    text = re.sub(r'[A-Za-z.,"?!״:׃]', '', text)
    # replace common hashem replacements with the tetragrammaton
    text = re.sub("(^|\s)([\u05de\u05e9\u05d5\u05db\u05dc\u05d1]?)(?:\u05d4['\u05f3]|\u05d9\u05d9)($|\s)",
                  "\\1\\2\u05d9\u05d4\u05d5\u05d4\\3", text)
    # replace common elokim replacement with elokim
    text = re.sub(
        "(^|\s)([\u05de\u05e9\u05d5\u05db\u05dc\u05d1]?)(?:\u05d0\u05dc\u05e7\u05d9\u05dd)($|\s)",
        "\\1\\2\u05d0\u05dc\u05d4\u05d9\u05dd\\3", text)
    words = []
    if len(text) != 0:
        # text = requests.post('https://prefix.dicta.org.il/api', data=json.dumps({'data': text})).text
        # text = re.sub(r'(?<=\s|"|\(|\[|-)[\u05d0-\u05ea]+\|', '', ' ' + text)  # remove prefixes
        text = re.sub('[^\u05d0-\u05ea"]', ' ', text)
        words = list(filter(lambda w: w not in stopwords, [re.sub('^\u05d5', '', w.replace('"', '')) for w in text.split()]))
    return words


def calculate_mean_tfidf():
    import math
    from sefaria.system.exceptions import InputError
    from tqdm import tqdm
    with open('data/hebrew_stopwords.txt', 'r') as fin:
        stopwords = set()
        for line in fin:
            stopwords.add(line.strip())
    ref_topic_links = RefTopicLinkSet({"is_sheet": False})
    ref_topic_map = defaultdict(list)
    ref_words_map = {}
    total = ref_topic_links.count()
    for l in tqdm(ref_topic_links, total=total, desc='process text'):
        ref_topic_map[l.toTopic] += [l.ref]
        if l.ref not in ref_words_map:
            try:
                oref = Ref(l.ref)
            except InputError:
                print(l.ref)
                continue

            ref_words_map[l.ref] = tokenize_words_for_tfidf(oref.text('he').as_string(), stopwords)

    # idf
    doc_word_counts = defaultdict(int)
    for topic, ref_list in tqdm(ref_topic_map.items(), desc='idf'):
        unique_words = set()
        for tref in ref_list:
            try:
                words = ref_words_map[tref]
            except KeyError:
                print("Dont have {}".format(tref))
                continue
            for w in words:
                if w not in unique_words:
                    doc_word_counts[w] += 1
                    unique_words.add(w)
    idf_dict = {}
    for w, count in doc_word_counts.items():
        idf_dict[w] = math.log2(len(ref_topic_map)/count)

    # tf-idf
    topic_tref_score_map = {}
    for topic, ref_list in ref_topic_map.items():
        total_tf = defaultdict(int)
        tref_tf = defaultdict(lambda: defaultdict(int))
        for tref in ref_list:
            words = ref_words_map.get(tref, [])
            for w in words:
                total_tf[w] += 1
                tref_tf[tref][w] += 1
        tfidf_dict = {}
        for w, tf in total_tf.items():
            tfidf_dict[w] = tf * idf_dict[w]
        for tref in ref_list:
            words = ref_words_map.get(tref, [])
            if len(words) == 0:
                topic_tref_score_map[(topic, tref)] = 0
                continue
            # calculate avg tfidf - tfidf for words that appear in this tref
            # so that tref can't influence score
            topic_tref_score_map[(topic, tref)] = sum((tfidf_dict[w] - tref_tf[tref].get(w, 0)*idf_dict[w]) for w in words)/len(words)

    return topic_tref_score_map, ref_topic_map


def calculate_other_ref_scores(ref_topic_map):
    from sefaria.system.exceptions import InputError
    from tqdm import tqdm

    LANGS_CHECKED = ['en', 'he']
    num_datasource_map = {}
    langs_available = {}
    ref_order_map = {}
    for topic, ref_list in tqdm(ref_topic_map.items(), desc='calculate other ref scores'):
        seg_ref_counter = defaultdict(int)
        tref_range_lists = {}
        for tref in ref_list:
            try:
                oref = Ref(tref)
            except InputError:
                continue
            tref_range_lists[tref] = [seg_ref.normal() for seg_ref in oref.range_list()]
            ref_order_map[(topic, tref)] = oref.order_id()
            langs_available[(topic, tref)] = [lang for lang in LANGS_CHECKED if oref.is_text_fully_available(lang)]
            for seg_ref in tref_range_lists[tref]:
                seg_ref_counter[seg_ref] += 1
        for tref in ref_list:
            range_list = tref_range_lists.get(tref, None)
            num_datasource_map[(topic, tref)] = 0 if range_list is None else max(seg_ref_counter[seg_ref] for seg_ref in range_list)
    return num_datasource_map, langs_available, ref_order_map


def update_link_orders():
    from tqdm import tqdm
    from sefaria.system.database import db
    topic_tref_score_map, ref_topic_map = calculate_mean_tfidf()
    num_datasource_map, langs_available, ref_order_map = calculate_other_ref_scores(ref_topic_map)
    ref_topic_links = RefTopicLinkSet()
    total = ref_topic_links.count()
    for l in tqdm(ref_topic_links, total=total, desc='update link orders'):
        if l.is_sheet:
            sheet = db.sheets.find_one({"id": int(l.ref.replace("Sheet ", ""))}, {"views": 1})
            setattr(l, 'order', {'views': sheet.get('views', 0)})
        else:
            key = (l.toTopic, l.ref)
            try:
                order = getattr(l, 'order', {})
                order.update({
                    'tfidf': topic_tref_score_map[key],
                    'numDatasource': num_datasource_map[key],
                    'availableLangs': langs_available[key],
                    'ref': ref_order_map[key]
                })
                setattr(l, 'order', order)
            except KeyError:
                print("KeyError", key)
        l.save()
