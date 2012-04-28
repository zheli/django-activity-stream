from datetime import datetime

from django.conf import settings
from django.test import TestCase
from django.template.loader import Template, Context
from django.contrib.auth.models import User, Group, AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site

from actstream.models import Action, Follow, model_stream, user_stream
from actstream.signals import action
from actstream.tests import ActivityBaseTestCase
from actstream.actions import follow, unfollow
from actstream.exceptions import ModelNotActionable


class TestAppTests(TestCase):

    def setUp(self):
        self.user = User.objects.create(username='test')
        action.send(self.user, verb='was created')

    def test_accessor(self):
        self.assertEqual(len(Action.objects.testfoo(self.user)), 1)
        self.assertEqual(len(Action.objects.testfoo(self.user, datetime(1970, 1, 1))), 0)

class ActivityTestCase(ActivityBaseTestCase):
    urls = 'actstream.urls'
    actstream_models = ('auth.User', 'auth.Group', 'sites.Site')

    def setUp(self):
        super(ActivityTestCase, self).setUp()
        self.group = Group.objects.create(name='CoolGroup')
        self.user1 = User.objects.get_or_create(username='admin')[0]
        self.user1.set_password('admin')
        self.user1.is_superuser = self.user1.is_staff = True
        self.user1.save()
        self.user2 = User.objects.get_or_create(username='Two')[0]

        # User1 joins group
        self.user1.groups.add(self.group)
        action.send(self.user1, verb='joined', target=self.group)

        # User1 follows User2
        follow(self.user1, self.user2)

        # User2 joins group
        self.user2.groups.add(self.group)
        action.send(self.user2, verb='joined', target=self.group)

        # User2 follows group
        follow(self.user2, self.group)

        # User1 comments on group
        # Use a site object here and predict the "__unicode__ method output"
        action.send(self.user1, verb='commented on', target=self.group)
        self.comment = Site.objects.create(
            domain="admin: Sweet Group!...")

        # Group responds to comment
        action.send(self.group, verb='responded to', target=self.comment)

    def test_aauser1(self):
        self.assertEqual(map(unicode, self.user1.actor_actions.all()), [
            u'admin commented on CoolGroup 0 minutes ago',
            u'admin started following Two 0 minutes ago',
            u'admin joined CoolGroup 0 minutes ago',
        ])

    def test_user2(self):
        self.assertEqual(map(unicode, Action.objects.actor(self.user2)), [
            u'Two started following CoolGroup 0 minutes ago',
            u'Two joined CoolGroup 0 minutes ago',
        ])

    def test_group(self):
        self.assertEqual(map(unicode, Action.objects.actor(self.group)),
            [u'CoolGroup responded to admin: Sweet Group!... 0 minutes ago'])

    def test_empty_follow_stream(self):
        unfollow(self.user1, self.user2)
        self.assert_(not user_stream(self.user1))

    def test_stream(self):
        self.assertEqual(map(unicode, Action.objects.user(self.user1)), [
            u'Two started following CoolGroup 0 minutes ago',
            u'Two joined CoolGroup 0 minutes ago',
        ])
        self.assertEqual(map(unicode, Action.objects.user(self.user2)),
            [u'CoolGroup responded to admin: Sweet Group!... 0 minutes ago'])

    def test_stream_stale_follows(self):
        """
        Action.objects.user() should ignore Follow objects with stale actor
        references.
        """
        self.user2.delete()
        self.assert_(not 'Two' in str(Action.objects.user(self.user1)))

    def test_rss(self):
        rss = self.client.get('/feed/').content
        self.assert_(rss.startswith('<?xml version="1.0" encoding="utf-8"?>\n'
            '<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">'))
        self.assert_(rss.find('Activity feed for your followed actors') > -1)

    def test_atom(self):
        atom = self.client.get('/feed/atom/').content
        self.assert_(atom.startswith('<?xml version="1.0" encoding="utf-8"?>\n'
            '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="%s">' %
                                     settings.LANGUAGE_CODE))
        self.assert_(atom.find('Activity feed for your followed actors') > -1)

    def test_action_object(self):
        action.send(self.user1, verb='created comment',
            action_object=self.comment, target=self.group)
        created_action = Action.objects.get(verb='created comment')

        self.assertEqual(created_action.actor, self.user1)
        self.assertEqual(created_action.action_object, self.comment)
        self.assertEqual(created_action.target, self.group)
        self.assertEqual(unicode(created_action),
            u'admin created comment admin: Sweet Group!... on CoolGroup 0 '
                'minutes ago')

    def test_doesnt_generate_duplicate_follow_records(self):
        g = Group.objects.get_or_create(name='DupGroup')[0]
        s = User.objects.get_or_create(username='dupuser')[0]

        f1 = follow(s, g)
        self.assertTrue(f1 is not None, "Should have received a new follow "
            "record")
        self.assertTrue(isinstance(f1, Follow), "Returns a Follow object")

        self.assertEquals(1, Follow.objects.filter(user=s, object_id=g.pk,
            content_type=ContentType.objects.get_for_model(g)).count(),
            "Should only have 1 follow record here")

        f2 = follow(s, g)
        self.assertEquals(1, Follow.objects.filter(user=s, object_id=g.pk,
            content_type=ContentType.objects.get_for_model(g)).count(),
            "Should still only have 1 follow record here")
        self.assertTrue(f2 is not None, "Should have received a Follow object")
        self.assertTrue(isinstance(f2, Follow), "Returns a Follow object")
        self.assertEquals(f1, f2, "Should have received the same Follow "
            "object that I first submitted")

    def test_zzzz_no_orphaned_actions(self):
        actions = self.user1.actor_actions.count()
        self.user2.delete()
        self.assertEqual(actions, self.user1.actor_actions.count() + 1)

    def test_generic_relation_accessors(self):
        self.assertEqual(self.user2.actor_actions.count(), 2)
        self.assertEqual(self.user2.target_actions.count(), 1)
        self.assertEqual(self.user2.action_object_actions.count(), 0)

    def test_bad_actionable_model(self):
        self.assertRaises(ModelNotActionable, follow, self.user1,
                          ContentType.objects.get_for_model(self.user1))

    def test_hidden_action(self):
        action = self.user1.actor_actions.all()[0]
        action.public = False
        action.save()
        self.assert_(not action in self.user1.actor_actions.public())

    def test_tag_follow_url(self):
        src = '{% load activity_tags %}{% follow_url user %}'
        output = Template(src).render(Context({'user': self.user1}))
        ct = ContentType.objects.get_for_model(User)
        self.assertEqual(output, '/follow/%s/%s/' % (ct.pk, self.user1.pk))

    def test_tag_follow_label(self):
        src = '{% load activity_tags %}'\
            '{% follow_label other_user yup nope %}'

        # Anonymous.
        output = Template(src).render(Context({'other_user': self.user1}))
        self.assertEqual(output, 'nope')

        output = Template(src).render(Context({'user': AnonymousUser(),
            'other_user': self.user1}))
        self.assertEqual(output, 'nope')

        # Non follower (user2 does not follow user1).
        output = Template(src).render(Context({'user': self.user2,
            'other_user': self.user1}))
        self.assertEqual(output, 'nope')

        # Follower (user1 follows user2).
        output = Template(src).render(Context({'user': self.user1,
            'other_user': self.user2}))
        self.assertEqual(output, 'yup')

    def test_model_actions_with_kwargs(self):
        """
        Testing the model_actions method of the ActionManager
        by passing kwargs
        """
        self.assertEqual(map(unicode, model_stream(self.user1, verb='commented on')), [
                u'admin commented on CoolGroup 0 minutes ago',
                ])

    def test_user_stream_with_kwargs(self):
        """
        Testing the user method of the ActionManager by passing additional
        filters in kwargs
        """
        self.assertEqual(map(unicode, Action.objects.user(self.user1, verb='joined')), [
                u'Two joined CoolGroup 0 minutes ago',
                ])

    def test_is_following_filter(self):
        src = '{% load activity_tags %}{% if user|is_following:group %}yup{% endif %}'
        self.assertEqual(Template(src).render(Context({
            'user': self.user2, 'group': self.group
        })), u'yup')
        self.assertEqual(Template(src).render(Context({
            'user': self.user1, 'group': self.group
        })), u'')
