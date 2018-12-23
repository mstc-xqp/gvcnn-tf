from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


import tensorflow as tf
import numpy as np

from slim.nets import inception_v2
from nets import googLeNet

slim = tf.contrib.slim


NUM_GROUP = 5



def refine_scheme(scheme):
    new_scheme = {}

    # for i, g in enumerate(scheme):
    try:
        new_scheme[g].append(i)
    except KeyError:
        new_scheme[g] = [i]

    return new_scheme


# average value per group
# TODO: weight will be modified according to equation2 correctly
def group_weight(scores, group_scheme):
    weight = {}

    for key, value in group_scheme.items():
        sum = 0
        n = len(group_scheme[key])
        for value in group_scheme[key]:
            sum += scores[value]

        # weight[key] = sum / n
        weight[key] = tf.cast(tf.div(sum, n), dtype=tf.float32)

    return weight


def grouping_scheme(view_discrimination_scores):
    group = []

    g0 = tf.constant(0, dtype=tf.float32)
    g1 = tf.constant(1/NUM_GROUP, dtype=tf.float32)
    g2 = tf.constant(2/NUM_GROUP, dtype=tf.float32)
    g3 = tf.constant(3/NUM_GROUP, dtype=tf.float32)
    g4 = tf.constant(4/NUM_GROUP, dtype=tf.float32)
    g5 = tf.constant(5/NUM_GROUP, dtype=tf.float32)

    for view_idx, score in enumerate(view_discrimination_scores):
        group_idx = tf.case(
            pred_fn_pairs=[
                (tf.logical_and(tf.greater_equal(score, g0), tf.less(score, g1)), lambda: tf.constant(1)),
                (tf.logical_and(tf.greater_equal(score, g1), tf.less(score, g2)), lambda: tf.constant(2)),
                (tf.logical_and(tf.greater_equal(score, g2), tf.less(score, g3)), lambda: tf.constant(3)),
                (tf.logical_and(tf.greater_equal(score, g3), tf.less(score, g4)), lambda: tf.constant(4)),
                (tf.logical_and(tf.greater_equal(score, g4), tf.less(score, g5)), lambda: tf.constant(5))],
            default=lambda: tf.constant(-1),
            exclusive=False)

        group.append(group_idx)

    return group


def _view_pooling(final_view_descriptors, group_scheme):

    '''
    Using the average pooling

    1. 같은 그룹에 속하는 net 를 tf.div(tf.add(a, b), num_same_group) ?
    2. 2d pooling ?

    :param group_scheme:
    :param final_view_descriptors:
    :return:
    '''

    group_descriptors = {}
    for key, value in group_scheme.items():
        view_desc = []
        for i in group_scheme[key]:
            view_desc.append(final_view_descriptors[i])

        n = len(group_scheme[key])
        group_descriptors[key] = tf.div(tf.add_n(view_desc), n)

    return group_descriptors


def _weighted_fusion(group_descriptors, group_weight):
    '''

    To generate the shape level description, all these group
    level descriptors should be further combined.

    :param group_descriptors:
    :return:
    '''

    up_list = []
    down_list = []
    for key, value in group_descriptors.items():
        up_list.append(tf.multiply(group_weight[key], group_descriptors[key]))
        down_list.append(group_descriptors[key])

    return tf.div(tf.add_n(up_list), tf.add_n(down_list))





def make_grouping_module(inputs,
                         is_training=True,
                         dropout_keep_prob=0.8,
                         reuse=tf.AUTO_REUSE,
                         scope='InceptionV2',
                         global_pool=True):

    """
    Raw View Descriptor Generation

    first part of the network (FCN) to get the raw descriptor in the view level.

    TODO: The “FCN” part is the top five convolutional layers of GoogLeNet. (mid-level representation ??)

    Extract the raw view descriptors. Compared with deeper CNN,
    shallow FCN could have more position information, which is needed for
    the followed grouping module and the deeper CNN will have the content information
    which could represent the view feature better.

    Args:
    inputs: N x V x H x W x C tensor
    scope:
    """

    input_views = []
    # FC layer to obtain the discrimination scores from raw view descriptors
    view_discrimination_scores = []

    n_views = inputs.get_shape().as_list()[1]

    # transpose views: (NxVxHxWxC) -> (VxNxHxWxC)
    views = tf.transpose(inputs, perm=[1, 0, 2, 3, 4])

    with tf.variable_scope(scope, 'InceptionV2', [inputs], reuse=reuse) as scope:
        with slim.arg_scope([slim.batch_norm, slim.dropout],
                            is_training=is_training):
            for i in range(n_views):
                batch_view = tf.gather(views, i)  # N x H x W x C
                # FCN
                net, end_points = \
                    inception_v2.inception_v2_base(batch_view, scope=scope)

                input_views.append(batch_view)

                # The grouping module aims to learn the group information
                # to assist in mining the relationship among views.
                with tf.variable_scope('Logits'):
                    if global_pool:
                        # Global average pooling.
                        net = tf.reduce_mean(net, [1, 2], keep_dims=True, name='global_pool')
                        end_points['global_pool'] = net
                    else:
                        # Pooling with a fixed kernel size.
                        kernel_size = inception_v2._reduced_kernel_size_for_small_input(net, [7, 7])
                        net = slim.avg_pool2d(net, kernel_size, padding='VALID',
                                              scope='AvgPool_1a_{}x{}'.format(*kernel_size))
                        end_points['AvgPool_1a'] = net

                    # ? X 1 x 1 x 1024
                    net = slim.dropout(net, dropout_keep_prob, scope='Dropout_1b')
                    net = slim.flatten(net)
                    # net = slim.fully_connected(net, 512)
                    logits = slim.fully_connected(net, 1, activation_fn=None)
                    end_points['Logits'] = logits

                    # 각각의 point of view 에 해당하는 batch size input 에 대해서
                    # 평균 score 로 표시한다.
                    score = tf.nn.sigmoid(tf.log(tf.abs(logits)))
                    score = tf.reduce_mean(score, axis=0)
                    score = tf.reshape(score, [])

                    view_discrimination_scores.append(score)

    # grouping weight/scheme
    g_scheme = grouping_scheme(view_discrimination_scores)

    # TODO: how to get grouping weight ?
    # group_weight = tf.zeros([2,2])
    # group_weight = grouping_weight(view_discrimination_scores, group_scheme)

    # d_score = tf.convert_to_tensor(view_discrimination_scores, dtype=tf.float32)
    # g_scheme = tf.convert_to_tensor(group_scheme)

    return view_discrimination_scores, g_scheme


def gvcnn(inputs,
          view_discrimination_scores,
          group_scheme,
          num_classes,
          is_training=True,
          dropout_keep_prob=0.8,
          prediction_fn=slim.softmax,
          reuse=tf.AUTO_REUSE,
          scope='InceptionV2',
          global_pool=True):
    '''
    The second part of the network (CNN) and the group module, are used to extract
    the final view descriptors together with the discrimination scores, separately.
    '''
    input_views = []
    final_view_descriptors = []  # Final View Descriptors

    n_views = inputs.get_shape().as_list()[1]
    # transpose views: (NxVxHxWxC) -> (VxNxHxWxC)
    views = tf.transpose(inputs, perm=[1, 0, 2, 3, 4])

    with tf.variable_scope(scope, 'InceptionV2', [inputs], reuse=reuse) as scope:
        with slim.arg_scope([slim.batch_norm, slim.dropout],
                            is_training=is_training):
            for i in range(n_views):
                batch_view = tf.gather(views, i)  # N x H x W x C

                net, end_points = \
                    inception_v2.inception_v2_base(batch_view, scope=scope)

                input_views.append(batch_view)
                with tf.variable_scope('Logits'):
                    if global_pool:
                        # Global average pooling.
                        net = tf.reduce_mean(net, [1, 2], keep_dims=True, name='global_pool')
                        end_points['global_pool'] = net
                    else:
                        # Pooling with a fixed kernel size.
                        kernel_size = inception_v2._reduced_kernel_size_for_small_input(net, [7, 7])
                        net = slim.avg_pool2d(net, kernel_size, padding='VALID',
                                              scope='AvgPool_1a_{}x{}'.format(*kernel_size))
                        end_points['AvgPool_1a'] = net

                    # ? x 1 x 1 x 1024
                    net = slim.dropout(net, keep_prob=dropout_keep_prob, scope='Dropout_1b')
                    # logits = slim.conv2d(net, num_classes, [1, 1], activation_fn=None,
                    #                      normalizer_fn=None, scope='Conv2d_1c_1x1')
                    # if spatial_squeeze:
                    #     logits = tf.squeeze(logits, [1, 2], name='SpatialSqueeze')
                    end_points['Logits'] = net

                    final_view_descriptors.append(net)

    # g_scheme = refine_scheme(group_scheme)
    g_scheme = tf.map_fn(refine_scheme, group_scheme)

    g_weight = group_weight(view_discrimination_scores, group_scheme)

    group_descriptors = _view_pooling(final_view_descriptors, g_scheme)
    shape_description = _weighted_fusion(group_descriptors, g_weight)

    # net = slim.flatten(shape_description)
    # logits = slim.fully_connected(net, num_classes, activation_fn=None)
    logits = slim.conv2d(shape_description, num_classes, [1, 1], activation_fn=None,
                         normalizer_fn=None, scope='Conv2d')
    logits = tf.squeeze(logits, [1, 2], name='SpatialSqueeze')

    pred = prediction_fn(logits, scope='Predictions')

    return pred


