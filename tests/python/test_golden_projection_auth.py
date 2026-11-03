import test_trend_volume_auth


class ProjectionGateway(test_trend_volume_auth.TrendVolumeGateway):
    paths = (
        '/data/golden-projection/daily/manifest.json',
        '/data/golden-projection/daily/status.json',
        '/data/golden-projection/daily/abcdef0123456789/screening.json',
        '/data/golden-projection/daily/abcdef0123456789/601086.json',
        '/data/golden-projection/daily/abcdef0123456789/sh000001.json',
        '/data/golden-projection/daily/abcdef0123456789/sz399006.json',
    )

    def test_projection_internal_objects_and_malformed_paths_are_not_exposed(self):
        self.auth.cognito.get_user.return_value = {'Username': 'projection-viewer'}
        for path in (
            '/data/golden-projection/daily/lease.json',
            '/data/golden-projection/daily/abcdef0123456789/batch.json',
            '/data/golden-projection/daily/abcdef0123456789/manifest.json',
            '/data/golden-projection/daily/ABCDEF0123456789/screening.json',
            '/data/golden-projection/daily/abcdef0123456789/../601086.json',
            '/data/golden-projection/daily/abcdef0123456789/601086.json/extra',
            '/data/golden-projection/daily/abcdef0123456789/60108.json',
            '/data/golden-projection/daily/abcdef0123456789/sh000002.json',
            '/data/golden-projection/daily/abcdef0123456789/sz399005.json',
            '/data/golden-projection/daily/abcdef0123456789/SH000001.json',
            '/data/golden-projection/daily/abcdef0123456789/sz0399006.json',
            '/data/golden-projection/daily/index-inputs/2026-11-03/sh000001.json',
            '/data/golden-projection/daily/index-snapshots/abcdef0123456789/sh000001.json',
            '/data/golden-projection/daily/index-snapshots/manifest.json',
        ):
            with self.subTest(path=path):
                self.assertEqual(self.auth.handler(self.event(path, token=self.token()), None)['statusCode'], 404)
        self.auth.s3.get_object.assert_not_called()
