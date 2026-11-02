import test_trend_volume_auth


class ProjectionGateway(test_trend_volume_auth.TrendVolumeGateway):
    paths = (
        '/data/golden-projection/daily/manifest.json',
        '/data/golden-projection/daily/status.json',
        '/data/golden-projection/daily/abcdef0123456789/screening.json',
        '/data/golden-projection/daily/abcdef0123456789/601086.json',
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
        ):
            with self.subTest(path=path):
                self.assertEqual(self.auth.handler(self.event(path, token=self.token()), None)['statusCode'], 404)
        self.auth.s3.get_object.assert_not_called()
