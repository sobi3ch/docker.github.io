from __future__ import absolute_import
from __future__ import unicode_literals

import random

import py
from docker.errors import NotFound

from .testcases import DockerClientTestCase
from compose.config import config
from compose.config.types import VolumeFromSpec
from compose.config.types import VolumeSpec
from compose.const import LABEL_PROJECT
from compose.container import Container
from compose.project import Project
from compose.service import ConvergenceStrategy
from compose.service import Net


def build_service_dicts(service_config):
    return config.load(
        config.ConfigDetails(
            'working_dir',
            [config.ConfigFile(None, service_config)]))


class ProjectTest(DockerClientTestCase):

    def test_containers(self):
        web = self.create_service('web')
        db = self.create_service('db')
        project = Project('composetest', [web, db], self.client)

        project.up()

        containers = project.containers()
        self.assertEqual(len(containers), 2)

    def test_containers_with_service_names(self):
        web = self.create_service('web')
        db = self.create_service('db')
        project = Project('composetest', [web, db], self.client)

        project.up()

        containers = project.containers(['web'])
        self.assertEqual(
            [c.name for c in containers],
            ['composetest_web_1'])

    def test_containers_with_extra_service(self):
        web = self.create_service('web')
        web_1 = web.create_container()

        db = self.create_service('db')
        db_1 = db.create_container()

        self.create_service('extra').create_container()

        project = Project('composetest', [web, db], self.client)
        self.assertEqual(
            set(project.containers(stopped=True)),
            set([web_1, db_1]),
        )

    def test_volumes_from_service(self):
        service_dicts = build_service_dicts({
            'data': {
                'image': 'busybox:latest',
                'volumes': ['/var/data'],
            },
            'db': {
                'image': 'busybox:latest',
                'volumes_from': ['data'],
            },
        })
        project = Project.from_config(
            name='composetest',
            config_data=service_dicts,
            client=self.client,
        )
        db = project.get_service('db')
        data = project.get_service('data')
        self.assertEqual(db.volumes_from, [VolumeFromSpec(data, 'rw')])

    def test_volumes_from_container(self):
        data_container = Container.create(
            self.client,
            image='busybox:latest',
            volumes=['/var/data'],
            name='composetest_data_container',
            labels={LABEL_PROJECT: 'composetest'},
        )
        project = Project.from_config(
            name='composetest',
            config_data=build_service_dicts({
                'db': {
                    'image': 'busybox:latest',
                    'volumes_from': ['composetest_data_container'],
                },
            }),
            client=self.client,
        )
        db = project.get_service('db')
        self.assertEqual(db._get_volumes_from(), [data_container.id + ':rw'])

    def test_get_network_does_not_exist(self):
        project = Project('composetest', [], self.client)
        assert project.get_network() is None

    def test_get_network(self):
        project_name = 'network_does_exist'
        network_name = '{}_default'.format(project_name)

        project = Project(project_name, [], self.client)
        self.client.create_network(network_name)
        self.addCleanup(self.client.remove_network, network_name)

        assert isinstance(project.get_network(), dict)
        assert project.get_network()['Name'] == network_name

    def test_net_from_service(self):
        project = Project.from_config(
            name='composetest',
            config_data=build_service_dicts({
                'net': {
                    'image': 'busybox:latest',
                    'command': ["top"]
                },
                'web': {
                    'image': 'busybox:latest',
                    'net': 'container:net',
                    'command': ["top"]
                },
            }),
            client=self.client,
        )

        project.up()

        web = project.get_service('web')
        net = project.get_service('net')
        self.assertEqual(web.net.mode, 'container:' + net.containers()[0].id)

    def test_net_from_container(self):
        net_container = Container.create(
            self.client,
            image='busybox:latest',
            name='composetest_net_container',
            command='top',
            labels={LABEL_PROJECT: 'composetest'},
        )
        net_container.start()

        project = Project.from_config(
            name='composetest',
            config_data=build_service_dicts({
                'web': {
                    'image': 'busybox:latest',
                    'net': 'container:composetest_net_container'
                },
            }),
            client=self.client,
        )

        project.up()

        web = project.get_service('web')
        self.assertEqual(web.net.mode, 'container:' + net_container.id)

    def test_start_pause_unpause_stop_kill_remove(self):
        web = self.create_service('web')
        db = self.create_service('db')
        project = Project('composetest', [web, db], self.client)

        project.start()

        self.assertEqual(len(web.containers()), 0)
        self.assertEqual(len(db.containers()), 0)

        web_container_1 = web.create_container()
        web_container_2 = web.create_container()
        db_container = db.create_container()

        project.start(service_names=['web'])
        self.assertEqual(set(c.name for c in project.containers()), set([web_container_1.name, web_container_2.name]))

        project.start()
        self.assertEqual(set(c.name for c in project.containers()),
                         set([web_container_1.name, web_container_2.name, db_container.name]))

        project.pause(service_names=['web'])
        self.assertEqual(set([c.name for c in project.containers() if c.is_paused]),
                         set([web_container_1.name, web_container_2.name]))

        project.pause()
        self.assertEqual(set([c.name for c in project.containers() if c.is_paused]),
                         set([web_container_1.name, web_container_2.name, db_container.name]))

        project.unpause(service_names=['db'])
        self.assertEqual(len([c.name for c in project.containers() if c.is_paused]), 2)

        project.unpause()
        self.assertEqual(len([c.name for c in project.containers() if c.is_paused]), 0)

        project.stop(service_names=['web'], timeout=1)
        self.assertEqual(set(c.name for c in project.containers()), set([db_container.name]))

        project.kill(service_names=['db'])
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 3)

        project.remove_stopped(service_names=['web'])
        self.assertEqual(len(project.containers(stopped=True)), 1)

        project.remove_stopped()
        self.assertEqual(len(project.containers(stopped=True)), 0)

    def test_create(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)

        project.create(['db'])
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 1)
        self.assertEqual(len(db.containers()), 0)
        self.assertEqual(len(db.containers(stopped=True)), 1)
        self.assertEqual(len(web.containers(stopped=True)), 0)

    def test_create_twice(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)

        project.create(['db', 'web'])
        project.create(['db', 'web'])
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 2)
        self.assertEqual(len(db.containers()), 0)
        self.assertEqual(len(db.containers(stopped=True)), 1)
        self.assertEqual(len(web.containers()), 0)
        self.assertEqual(len(web.containers(stopped=True)), 1)

    def test_create_with_links(self):
        db = self.create_service('db')
        web = self.create_service('web', links=[(db, 'db')])
        project = Project('composetest', [db, web], self.client)

        project.create(['web'])
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 2)
        self.assertEqual(len(db.containers()), 0)
        self.assertEqual(len(db.containers(stopped=True)), 1)
        self.assertEqual(len(web.containers()), 0)
        self.assertEqual(len(web.containers(stopped=True)), 1)

    def test_create_strategy_always(self):
        db = self.create_service('db')
        project = Project('composetest', [db], self.client)
        project.create(['db'])
        old_id = project.containers(stopped=True)[0].id

        project.create(['db'], strategy=ConvergenceStrategy.always)
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 1)

        db_container = project.containers(stopped=True)[0]
        self.assertNotEqual(db_container.id, old_id)

    def test_create_strategy_never(self):
        db = self.create_service('db')
        project = Project('composetest', [db], self.client)
        project.create(['db'])
        old_id = project.containers(stopped=True)[0].id

        project.create(['db'], strategy=ConvergenceStrategy.never)
        self.assertEqual(len(project.containers()), 0)
        self.assertEqual(len(project.containers(stopped=True)), 1)

        db_container = project.containers(stopped=True)[0]
        self.assertEqual(db_container.id, old_id)

    def test_project_up(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'])
        self.assertEqual(len(project.containers()), 1)
        self.assertEqual(len(db.containers()), 1)
        self.assertEqual(len(web.containers()), 0)

    def test_project_up_starts_uncreated_services(self):
        db = self.create_service('db')
        web = self.create_service('web', links=[(db, 'db')])
        project = Project('composetest', [db, web], self.client)
        project.up(['db'])
        self.assertEqual(len(project.containers()), 1)

        project.up()
        self.assertEqual(len(project.containers()), 2)
        self.assertEqual(len(db.containers()), 1)
        self.assertEqual(len(web.containers()), 1)

    def test_recreate_preserves_volumes(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/etc')])
        project = Project('composetest', [web, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'])
        self.assertEqual(len(project.containers()), 1)
        old_db_id = project.containers()[0].id
        db_volume_path = project.containers()[0].get('Volumes./etc')

        project.up(strategy=ConvergenceStrategy.always)
        self.assertEqual(len(project.containers()), 2)

        db_container = [c for c in project.containers() if 'db' in c.name][0]
        self.assertNotEqual(db_container.id, old_db_id)
        self.assertEqual(db_container.get('Volumes./etc'), db_volume_path)

    def test_project_up_with_no_recreate_running(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'])
        self.assertEqual(len(project.containers()), 1)
        old_db_id = project.containers()[0].id
        container, = project.containers()
        db_volume_path = container.get_mount('/var/db')['Source']

        project.up(strategy=ConvergenceStrategy.never)
        self.assertEqual(len(project.containers()), 2)

        db_container = [c for c in project.containers() if 'db' in c.name][0]
        self.assertEqual(db_container.id, old_db_id)
        self.assertEqual(
            db_container.get_mount('/var/db')['Source'],
            db_volume_path)

    def test_project_up_with_no_recreate_stopped(self):
        web = self.create_service('web')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        project = Project('composetest', [web, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'])
        project.kill()

        old_containers = project.containers(stopped=True)

        self.assertEqual(len(old_containers), 1)
        old_container, = old_containers
        old_db_id = old_container.id
        db_volume_path = old_container.get_mount('/var/db')['Source']

        project.up(strategy=ConvergenceStrategy.never)

        new_containers = project.containers(stopped=True)
        self.assertEqual(len(new_containers), 2)
        self.assertEqual([c.is_running for c in new_containers], [True, True])

        db_container = [c for c in new_containers if 'db' in c.name][0]
        self.assertEqual(db_container.id, old_db_id)
        self.assertEqual(
            db_container.get_mount('/var/db')['Source'],
            db_volume_path)

    def test_project_up_without_all_services(self):
        console = self.create_service('console')
        db = self.create_service('db')
        project = Project('composetest', [console, db], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up()
        self.assertEqual(len(project.containers()), 2)
        self.assertEqual(len(db.containers()), 1)
        self.assertEqual(len(console.containers()), 1)

    def test_project_up_starts_links(self):
        console = self.create_service('console')
        db = self.create_service('db', volumes=[VolumeSpec.parse('/var/db')])
        web = self.create_service('web', links=[(db, 'db')])

        project = Project('composetest', [web, db, console], self.client)
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['web'])
        self.assertEqual(len(project.containers()), 2)
        self.assertEqual(len(web.containers()), 1)
        self.assertEqual(len(db.containers()), 1)
        self.assertEqual(len(console.containers()), 0)

    def test_project_up_starts_depends(self):
        project = Project.from_config(
            name='composetest',
            config_data=build_service_dicts({
                'console': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                },
                'data': {
                    'image': 'busybox:latest',
                    'command': ["top"]
                },
                'db': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                    'volumes_from': ['data'],
                },
                'web': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                    'links': ['db'],
                },
            }),
            client=self.client,
        )
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['web'])
        self.assertEqual(len(project.containers()), 3)
        self.assertEqual(len(project.get_service('web').containers()), 1)
        self.assertEqual(len(project.get_service('db').containers()), 1)
        self.assertEqual(len(project.get_service('data').containers()), 1)
        self.assertEqual(len(project.get_service('console').containers()), 0)

    def test_project_up_with_no_deps(self):
        project = Project.from_config(
            name='composetest',
            config_data=build_service_dicts({
                'console': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                },
                'data': {
                    'image': 'busybox:latest',
                    'command': ["top"]
                },
                'db': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                    'volumes_from': ['data'],
                },
                'web': {
                    'image': 'busybox:latest',
                    'command': ["top"],
                    'links': ['db'],
                },
            }),
            client=self.client,
        )
        project.start()
        self.assertEqual(len(project.containers()), 0)

        project.up(['db'], start_deps=False)
        self.assertEqual(len(project.containers(stopped=True)), 2)
        self.assertEqual(len(project.get_service('web').containers()), 0)
        self.assertEqual(len(project.get_service('db').containers()), 1)
        self.assertEqual(len(project.get_service('data').containers()), 0)
        self.assertEqual(len(project.get_service('data').containers(stopped=True)), 1)
        self.assertEqual(len(project.get_service('console').containers()), 0)

    def test_project_up_with_custom_network(self):
        network_name = 'composetest-custom'

        self.client.create_network(network_name)
        self.addCleanup(self.client.remove_network, network_name)

        web = self.create_service('web', net=Net(network_name))
        project = Project('composetest', [web], self.client, use_networking=True)
        project.up()

        assert project.get_network() is None

    def test_unscale_after_restart(self):
        web = self.create_service('web')
        project = Project('composetest', [web], self.client)

        project.start()

        service = project.get_service('web')
        service.scale(1)
        self.assertEqual(len(service.containers()), 1)
        service.scale(3)
        self.assertEqual(len(service.containers()), 3)
        project.up()
        service = project.get_service('web')
        self.assertEqual(len(service.containers()), 3)
        service.scale(1)
        self.assertEqual(len(service.containers()), 1)
        project.up()
        service = project.get_service('web')
        self.assertEqual(len(service.containers()), 1)
        # does scale=0 ,makes any sense? after recreating at least 1 container is running
        service.scale(0)
        project.up()
        service = project.get_service('web')
        self.assertEqual(len(service.containers()), 1)

    def test_project_up_volumes(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)
        config_data = config.Config(
            version=2, services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }], volumes={vol_name: {'driver': 'local'}}
        )

        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.up()
        self.assertEqual(len(project.containers()), 1)

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

    def test_project_up_logging_with_multiple_files(self):
        base_file = config.ConfigFile(
            'base.yml',
            {
                'version': 2,
                'services': {
                    'simple': {'image': 'busybox:latest', 'command': 'top'},
                    'another': {
                        'image': 'busybox:latest',
                        'command': 'top',
                        'logging': {
                            'driver': "json-file",
                            'options': {
                                'max-size': "10m"
                            }
                        }
                    }
                }

            })
        override_file = config.ConfigFile(
            'override.yml',
            {
                'version': 2,
                'services': {
                    'another': {
                        'logging': {
                            'driver': "none"
                        }
                    }
                }

            })
        details = config.ConfigDetails('.', [base_file, override_file])

        tmpdir = py.test.ensuretemp('logging_test')
        self.addCleanup(tmpdir.remove)
        with tmpdir.as_cwd():
            config_data = config.load(details)
        project = Project.from_config(
            name='composetest', config_data=config_data, client=self.client
        )
        project.up()
        containers = project.containers()
        self.assertEqual(len(containers), 2)

        another = project.get_service('another').containers()[0]
        log_config = another.get('HostConfig.LogConfig')
        self.assertTrue(log_config)
        self.assertEqual(log_config.get('Type'), 'none')

    def test_initialize_volumes(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)
        config_data = config.Config(
            version=2, services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }], volumes={vol_name: {}}
        )

        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.initialize_volumes()

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

    def test_project_up_implicit_volume_driver(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)
        config_data = config.Config(
            version=2, services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }], volumes={vol_name: {}}
        )

        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.up()

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

    def test_initialize_volumes_invalid_volume_driver(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))

        config_data = config.Config(
            version=2, services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }], volumes={vol_name: {'driver': 'foobar'}}
        )

        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        with self.assertRaises(config.ConfigurationError):
            project.initialize_volumes()

    def test_initialize_volumes_updated_driver(self):
        vol_name = '{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)

        config_data = config.Config(
            version=2, services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }], volumes={vol_name: {'driver': 'local'}}
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.initialize_volumes()

        volume_data = self.client.inspect_volume(full_vol_name)
        self.assertEqual(volume_data['Name'], full_vol_name)
        self.assertEqual(volume_data['Driver'], 'local')

        config_data = config_data._replace(
            volumes={vol_name: {'driver': 'smb'}}
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        with self.assertRaises(config.ConfigurationError) as e:
            project.initialize_volumes()
        assert 'Configuration for volume {0} specifies driver smb'.format(
            vol_name
        ) in str(e.exception)

    def test_initialize_volumes_user_created_volumes(self):
        # Use composetest_ prefix so it gets garbage-collected in tearDown()
        vol_name = 'composetest_{0:x}'.format(random.getrandbits(32))
        full_vol_name = 'composetest_{0}'.format(vol_name)
        self.client.create_volume(vol_name)
        config_data = config.Config(
            version=2, services=[{
                'name': 'web',
                'image': 'busybox:latest',
                'command': 'top'
            }], volumes={vol_name: {'driver': 'local'}}
        )
        project = Project.from_config(
            name='composetest',
            config_data=config_data, client=self.client
        )
        project.initialize_volumes()

        with self.assertRaises(NotFound):
            self.client.inspect_volume(full_vol_name)
