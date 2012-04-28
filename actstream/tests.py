from random import choice

from django.db import connection
from django.db.models import get_model
from django.test import TestCase
from django.conf import settings
from django.contrib.auth.models import User, AnonymousUser, Group
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.template.loader import Template, Context

from actstream.models import Action, Follow, model_stream, user_stream,\
    setup_generic_relations
from actstream.actions import follow, unfollow
from actstream.exceptions import ModelNotActionable
from actstream.signals import action
from actstream import settings as actstream_settings

class LTE(int):
    def __new__(cls, n):
        obj = super(LTE, cls).__new__(cls, n)
        obj.n = n
        return obj

    def __eq__(self, other):
        return other <= self.n

    def __repr__(self):
        return "<= %s" % self.n

class ActivityBaseTestCase(TestCase):
    actstream_models = ()

    def setUp(self):
        self.old_MODELS = actstream_settings.MODELS
        actstream_settings.MODELS = {}
        for model in self.actstream_models:
            actstream_settings.MODELS[model.lower()] = \
                get_model(*model.split('.'))
        setup_generic_relations()

    def tearDown(self):
        actstream_settings.MODELS = self.old_MODELS
        
class ZombieTest(ActivityBaseTestCase):
    actstream_models = ('auth.User',)
    human = 10
    zombie = 1

    def setUp(self):
        super(ZombieTest, self).setUp()
        settings.DEBUG = True

        player_generator = lambda n, count: [User.objects.create(
            username='%s%d' % (n, i)) for i in range(count)]

        self.humans = player_generator('human', self.human)
        self.zombies = player_generator('zombie', self.zombie)

        self.zombie_apocalypse()

    def tearDown(self):
        settings.DEBUG = False
        super(ZombieTest, self).tearDown()

    def zombie_apocalypse(self):
        humans = self.humans[:]
        zombies = self.zombies[:]
        while humans:
            for z in self.zombies:
                victim = choice(humans)
                humans.remove(victim)
                zombies.append(victim)
                action.send(z, verb='killed', target=victim)
                if not humans:
                    break

    def check_query_count(self, queryset):
        ci = len(connection.queries)

        result = list([map(unicode, (x.actor, x.target, x.action_object))
            for x in queryset])
        self.assertTrue(len(connection.queries) - ci <= 4,
            'Too many queries, got %d expected no more than 4' %
                len(connection.queries))
        return result

    def test_query_count(self):
        queryset = model_stream(User)
        result = self.check_query_count(queryset)
        self.assertEqual(len(result), 10)

    def test_query_count_sliced(self):
        queryset = model_stream(User)[:5]
        result = self.check_query_count(queryset)
        self.assertEqual(len(result), 5)


class GFKManagerTestCase(TestCase):

    def setUp(self):
        self.user_ct = ContentType.objects.get_for_model(User)
        self.group_ct = ContentType.objects.get_for_model(Group)
        self.group, _ = Group.objects.get_or_create(name='CoolGroup')
        self.user1, _ = User.objects.get_or_create(username='admin')
        self.user2, _ = User.objects.get_or_create(username='Two')
        self.user3, _ = User.objects.get_or_create(username='Three')
        self.user4, _ = User.objects.get_or_create(username='Four')
        Action.objects.get_or_create(
            actor_content_type=self.user_ct,
            actor_object_id=self.user1.id,
            verb='followed',
            target_content_type=self.user_ct,
            target_object_id=self.user2.id
        )
        Action.objects.get_or_create(
            actor_content_type=self.user_ct,
            actor_object_id=self.user1.id,
            verb='followed',
            target_content_type=self.user_ct,
            target_object_id=self.user3.id
        )
        Action.objects.get_or_create(
            actor_content_type=self.user_ct,
            actor_object_id=self.user1.id,
            verb='followed',
            target_content_type=self.user_ct,
            target_object_id=self.user4.id
        )
        Action.objects.get_or_create(
            actor_content_type=self.user_ct,
            actor_object_id=self.user1.id,
            verb='joined',
            target_content_type=self.group_ct,
            target_object_id=self.group.id
        )

    def test_fetch_generic_relations(self):
        # baseline without fetch_generic_relations
        _actions = Action.objects.filter(actor_content_type=self.user_ct,
            actor_object_id=self.user1.id)
        actions = lambda: _actions._clone()
        num_content_types = len(set(actions().values_list(
            'target_content_type_id', flat=True)))
        n = actions().count()

        # compare to fetching only 1 generic relation
        self.assertNumQueries(LTE(n + 1),
            lambda: [a.target for a in actions()])
        self.assertNumQueries(LTE(num_content_types + 2),
            lambda: [a.target for a in
                actions().fetch_generic_relations('target')])

        action_targets = [(a.id, a.target) for a in actions()]
        action_targets_fetch_generic = [(a.id, a.target) for a in
                actions().fetch_generic_relations('target')]
        self.assertEqual(action_targets, action_targets_fetch_generic)

        # compare to fetching all generic relations
        num_content_types = len(set(sum(actions().values_list(
            'actor_content_type_id', 'target_content_type_id'), ())))
        self.assertNumQueries(LTE(2 * n + 1),
            lambda: [(a.actor, a.target) for a in actions()])
        self.assertNumQueries(LTE(num_content_types + 2),
            lambda: [(a.actor, a.target) for a in
                actions().fetch_generic_relations()])

        action_actor_targets = [(a.id, a.actor, a.target) for a in actions()]
        action_actor_targets_fetch_generic_all = [
            (a.id, a.actor, a.target) for a in
                actions().fetch_generic_relations()]
        self.assertEqual(action_actor_targets,
            action_actor_targets_fetch_generic_all)

        # fetch only 1 generic relation, but access both gfks
        self.assertNumQueries(LTE(n + num_content_types + 2),
            lambda: [(a.actor, a.target) for a in
                actions().fetch_generic_relations('target')])
        action_actor_targets_fetch_generic_target = [
            (a.id, a.actor, a.target) for a in
                actions().fetch_generic_relations('target')]
        self.assertEqual(action_actor_targets,
            action_actor_targets_fetch_generic_target)
