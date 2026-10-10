from datetime import timedelta
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from apps.monitoring.models import ClusterItem, ContentItem, Keyword, KeywordMatch, Source, SourceGroup, SystemSetting, Topic, TrendCluster
from apps.monitoring.trend_services import create_analysis_run, execute_analysis, link, score_cluster, similarity

def src(name, platform='telegram', group=None, importance=Source.Importance.GENERAL):
    if platform=='telegram': return Source.objects.create(platform='telegram',public_url=f'https://t.me/s/{name}channel',display_name=name,group=group,importance=importance)
    return Source.objects.create(platform='x',identifier=name,display_name=name,group=group,importance=importance)
def item(source,text,external): return ContentItem.objects.create(source=source,external_id=str(external),published_at=timezone.now()-timedelta(minutes=5),raw_text=text,normalized_text=text)

@pytest.mark.django_db
def test_keyword_matching_exclusion_and_idempotent_clusters():
    topic=Topic.objects.create(name='خبر'); Keyword.objects.create(topic=topic,phrase='فوری',match_type='contains',weight=50)
    a=item(src('a'),'خبر فوری درباره بازار',1); b=item(src('b'),'خبر فوری درباره بازار',2)
    run=create_analysis_run();execute_analysis(run); assert TrendCluster.objects.count()==1
    run2=create_analysis_run();execute_analysis(run2); assert TrendCluster.objects.count()==1
    Keyword.objects.create(topic=topic,phrase='شایعه',match_type='contains',is_excluded=True)
    item(src('c'),'خبر فوری شایعه',3);execute_analysis(create_analysis_run()); assert TrendCluster.objects.get().item_count==2

@pytest.mark.django_db
def test_near_similarity_independent_sources_platform_bonus_and_status():
    topic=Topic.objects.create(name='فناوری');Keyword.objects.create(topic=topic,phrase='سامانه',match_type='contains',weight=100)
    group=SourceGroup.objects.create(name='هم‌گروه'); a=item(src('a',group=group),'سامانه جدید امروز رونمایی شد',1); b=item(src('b',group=group),'سامانه جدید امروز رونمایی شد',2); c=item(src('xc','x'),'سامانه جدید امروز رونمایی شد',3)
    cluster=None;execute_analysis(create_analysis_run());cluster=TrendCluster.objects.get();assert cluster.independent_source_count==2;assert 'x' in cluster.platforms;assert cluster.score>=0
    assert similarity('سامانه جدید امروز','سامانه جدید امروز')==1

@pytest.mark.django_db
def test_trend_staff_actions_are_protected(client):
    topic=Topic.objects.create(name='t'); cluster=TrendCluster.objects.create(topic=topic,first_published_at=timezone.now(),last_published_at=timezone.now())
    url=reverse('monitoring:trend_cluster_status',args=[cluster.pk,'confirmed']);assert client.post(url).status_code==302
    staff=get_user_model().objects.create_user('trendstaff',password='password',is_staff=True);client.force_login(staff);assert client.post(url).status_code==302;cluster.refresh_from_db();assert cluster.status=='confirmed'

@pytest.mark.django_db
@pytest.mark.parametrize('status',['confirmed','dismissed','suppressed'])
def test_human_status_is_preserved_on_analysis(status):
    topic=Topic.objects.create(name='human'+status);Keyword.objects.create(topic=topic,phrase='خبر',match_type='contains')
    c=TrendCluster.objects.create(topic=topic,status=status,first_published_at=timezone.now()-timedelta(days=2),last_published_at=timezone.now()-timedelta(days=2))
    execute_analysis(create_analysis_run());c.refresh_from_db();assert c.status==status

@pytest.mark.django_db
def test_machine_cluster_resolves_and_hashtag_alone_does_not_link():
    topic=Topic.objects.create(name='machine');Keyword.objects.create(topic=topic,phrase='خبر',match_type='contains')
    old=TrendCluster.objects.create(topic=topic,status='candidate',first_published_at=timezone.now()-timedelta(days=2),last_published_at=timezone.now()-timedelta(days=2))
    a=item(src('h1'),'خبر اقتصاد #عمومی',1);b=item(src('h2'),'خبر ورزش #عمومی',2)
    execute_analysis(create_analysis_run());old.refresh_from_db();assert old.status=='resolved';assert TrendCluster.objects.filter(topic=topic,status__in=['candidate','trending']).count()==2


@pytest.mark.django_db
def test_shared_delivery_footer_does_not_link_unrelated_news():
    footer="دریافت آخرین اخبار سایت ایتا بله روبیکا سروش پلاس"
    first=item(src('footer1'),f"گزارش دادگاه و پرونده متهمان در کرج با جزئیات تازه منتشر شد {footer}",1)
    second=item(src('footer2'),f"گزارش دانشگاه افسری و پذیرش دانشجو از طریق کنکور سراسری منتشر شد {footer}",2)

    assert link(first,second,SystemSetting.get_solo())==(0.0,"")


@pytest.mark.django_db
def test_cluster_score_counts_only_keywords_from_its_topic():
    topic=Topic.objects.create(name="خانواده")
    other_topic=Topic.objects.create(name="سیاست")
    content=item(src("topicscore"),"جوانان و آمریکا",1)
    own_keyword=Keyword.objects.create(topic=topic,phrase="جوان",match_type="contains",weight=10)
    other_keyword=Keyword.objects.create(topic=other_topic,phrase="آمریکا",match_type="contains",weight=100)
    KeywordMatch.objects.create(content_item=content,keyword=own_keyword,topic=topic,match_type="contains",matched_phrase="جوان")
    KeywordMatch.objects.create(content_item=content,keyword=other_keyword,topic=other_topic,match_type="contains",matched_phrase="آمریکا")
    record=TrendCluster.objects.create(topic=topic,first_published_at=content.published_at,last_published_at=content.published_at,representative=content)
    ClusterItem.objects.create(cluster=record,content_item=content,link_type="exact")

    score_cluster(record,SystemSetting.get_solo())

    assert "1 کلیدواژه منطبق" in record.score_breakdown["دلایل"]


@pytest.mark.django_db
def test_bridge_merges_machine_clusters_into_oldest_and_recalculates():
    topic=Topic.objects.create(name='ادغام ماشینی')
    Keyword.objects.create(topic=topic,phrase='خبر',match_type='contains')
    first=item(src('mergea'),'خبر اقتصاد https://source-a.example/story',1)
    second=item(src('mergeb'),'خبر سلامت https://source-b.example/story',2)
    execute_analysis(create_analysis_run())
    clusters=list(TrendCluster.objects.filter(topic=topic).order_by('created_at','pk'))
    assert len(clusters)==2
    primary,secondary=clusters
    bridge=item(src('mergec'),'خبر پیوند https://source-a.example/story https://source-b.example/story',3)
    execute_analysis(create_analysis_run())
    primary.refresh_from_db();secondary.refresh_from_db()
    assert primary.pk==clusters[0].pk
    assert secondary.status==TrendCluster.Status.RESOLVED
    assert secondary.merged_into_id==primary.pk
    assert primary.item_count==3
    assert primary.items.count()==3
    assert ClusterItem.objects.filter(cluster=primary,content_item=bridge).count()==1
    assert primary.independent_source_count==3
    assert primary.platforms==['telegram']
    assert primary.first_published_at<=primary.last_published_at
    assert primary.score==primary.score_breakdown['امتیاز']


@pytest.mark.django_db
def test_merge_is_idempotent_and_keeps_trace_in_cluster_detail(client):
    topic=Topic.objects.create(name='ردیابی ادغام')
    Keyword.objects.create(topic=topic,phrase='خبر',match_type='contains')
    item(src('tracea'),'خبر الف https://trace-a.example/post',1)
    item(src('traceb'),'خبر ب https://trace-b.example/post',2)
    execute_analysis(create_analysis_run())
    primary,secondary=list(TrendCluster.objects.filter(topic=topic).order_by('created_at','pk'))
    item(src('tracec'),'خبر پیوند https://trace-a.example/post https://trace-b.example/post',3)
    execute_analysis(create_analysis_run())
    execute_analysis(create_analysis_run())
    primary.refresh_from_db();secondary.refresh_from_db()
    assert TrendCluster.objects.filter(topic=topic).count()==2
    assert secondary.merged_into_id==primary.pk
    assert primary.items.count()==3
    staff=get_user_model().objects.create_user('mergeviewer',password='password',is_staff=True)
    client.force_login(staff)
    assert 'خوشه در این خوشه ادغام شده است' in client.get(reverse('monitoring:trend_cluster_detail',args=[primary.pk])).content.decode()
    assert 'خوشه اصلی' in client.get(reverse('monitoring:trend_cluster_detail',args=[secondary.pk])).content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize('human_status',[TrendCluster.Status.CONFIRMED,TrendCluster.Status.DISMISSED,TrendCluster.Status.SUPPRESSED])
def test_merge_never_crosses_topics_or_human_clusters(human_status):
    first_topic=Topic.objects.create(name='موضوع اول')
    second_topic=Topic.objects.create(name='موضوع دوم')
    Keyword.objects.create(topic=first_topic,phrase='اول',match_type='contains')
    Keyword.objects.create(topic=second_topic,phrase='دوم',match_type='contains')
    first=TrendCluster.objects.create(topic=first_topic,status=human_status,first_published_at=timezone.now(),last_published_at=timezone.now())
    second=TrendCluster.objects.create(topic=second_topic,status=TrendCluster.Status.CANDIDATE,first_published_at=timezone.now(),last_published_at=timezone.now())
    shared=item(src('differenttopic'),'اول دوم https://shared.example/post',1)
    first.representative=shared;first.save(update_fields=['representative'])
    second.representative=shared;second.save(update_fields=['representative'])
    ClusterItem.objects.create(cluster=first,content_item=shared,link_type='exact')
    ClusterItem.objects.create(cluster=second,content_item=shared,link_type='exact')
    execute_analysis(create_analysis_run())
    first.refresh_from_db();second.refresh_from_db()
    assert first.status==human_status
    assert first.merged_into_id is None
    assert second.topic_id==second_topic.id
    assert second.merged_into_id is None


@pytest.mark.django_db
def test_three_general_sources_are_trending_and_single_source_is_candidate():
    topic = Topic.objects.create(name='خانواده')
    Keyword.objects.create(topic=topic, phrase='ازدواج', match_type='contains')

    src1 = src('ch1')
    src2 = src('ch2')
    src3 = src('ch3')
    src4 = src('ch4')

    text_common_1 = 'آمار جدید ازدواج و طلاق در سال جاری نشان‌دهنده تغییرات مهم در سبک زندگی است'
    text_common_2 = 'آمار جدید ازدواج و طلاق در سال جاری نشان‌دهنده تغییرات مهم در سبک زندگی است'
    text_unique = 'نکات مهم درباره ازدواج موفق و انتخاب همسر مناسب در خانواده'

    item(src1, text_common_1, 101)
    item(src2, text_common_2, 102)
    item(src3, text_common_1, 103)
    item(src4, text_unique, 104)

    run = create_analysis_run()
    execute_analysis(run, topics=[topic])

    shared_cluster = TrendCluster.objects.filter(topic=topic, independent_source_count__gte=3).first()
    assert shared_cluster is not None
    assert shared_cluster.status == TrendCluster.Status.TRENDING
    assert shared_cluster.items.count() == 3

    single_cluster = TrendCluster.objects.filter(topic=topic, independent_source_count=1).first()
    assert single_cluster is not None
    assert single_cluster.status == TrendCluster.Status.CANDIDATE


@pytest.mark.django_db
def test_single_special_source_is_trending():
    topic = Topic.objects.create(name='بورس')
    Keyword.objects.create(topic=topic, phrase='بازار سرمایه', match_type='contains')
    special = src('specialnews', importance=Source.Importance.SPECIAL)
    item(special, 'خبر مهم امروز درباره بازار سرمایه', 150)

    execute_analysis(create_analysis_run(), topics=[topic])

    cluster = TrendCluster.objects.get(topic=topic)
    assert cluster.independent_source_count == 1
    assert cluster.status == TrendCluster.Status.TRENDING


@pytest.mark.django_db
def test_cross_channel_watermark_and_handle_stripping():
    topic = Topic.objects.create(name='طلاق')
    Keyword.objects.create(topic=topic, phrase='طلاق', match_type='contains')

    src_akhbar = src('akhbar')
    src_gizmiz = src('gizmiz')
    src_third = src('thirdnews')

    text1 = '♦️ نرخ طلاق سال 1405 بالا رفت\n\nرئیس سازمان ثبت احوال کشور از افزایش آمار طلاق در سال گذشته خبر داد...\n\n🆔 @AkhbareFori'
    text2 = 'نرخ طلاق سال 1405 بالا رفت!\n\nرئیس سازمان ثبت احوال کشور از افزایش آمار طلاق در سال گذشته خبر داد...\n\n@GizmizTel 💯'

    item(src_akhbar, text1, 201)
    item(src_gizmiz, text2, 202)
    item(src_third, text1, 203)

    run = create_analysis_run()
    execute_analysis(run, topics=[topic])

    cluster = TrendCluster.objects.filter(topic=topic, independent_source_count__gte=2).first()
    assert cluster is not None
    assert cluster.status == TrendCluster.Status.TRENDING
    assert cluster.item_count == 3
    assert cluster.independent_source_count == 3


@pytest.mark.django_db
def test_headline_match_links_shared_news():
    topic = Topic.objects.create(name='وام')
    Keyword.objects.create(topic=topic, phrase='ازدواج', match_type='contains')

    src1 = src('news1')
    src2 = src('news2')
    src3 = src('news3')

    post1 = 'ثبت‌نام وام ازدواج و جزئیات پرداخت به متقاضیان در سال جاری آغاز شد\nطبق بخشنامه جدید بانک مرکزی متقاضیان می‌توانند از فردا اقدام کنند.'
    post2 = 'ثبت‌نام وام ازدواج و جزئیات پرداخت به متقاضیان در سال جاری آغاز شد\nبانک مرکزی در اطلاعیه‌ای جزئیات و ضوابط را تشریح کرد.'

    item(src1, post1, 301)
    item(src2, post2, 302)
    item(src3, post1, 303)

    run = create_analysis_run()
    execute_analysis(run, topics=[topic])

    cluster = TrendCluster.objects.filter(topic=topic, independent_source_count__gte=2).first()
    assert cluster is not None
    assert cluster.status == TrendCluster.Status.TRENDING
    assert cluster.item_count == 3

