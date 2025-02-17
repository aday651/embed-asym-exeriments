import tensorflow as tf
from custom_scripts.node_classifier_modified import make_node_classifier


def make_skipgram(**kwargs):
    """ Uses the skipgram objective for relational data

    Returns
    -------
    A model function for skipgram edge prediction (with a nonsense vertex classifier attached for testing convenience)
    """

    def make_label_logits(embeddings, features, mode, params):
        # TODO: fix this. What's going on? Basically, the size of 
        # embeddings is dynamic, and so we need a way to properly 
        # handle this in order to set the size of this zeros array...
        #return tf.zeros([tf.shape(embeddings)[0], params['n_labels']],
        #                dtype=tf.float32)
        return tf.zeros([embeddings.get_shape().as_list()[0], 
                         params['n_labels']],
                        dtype=tf.float32)

    def make_no_label_loss(logits, present_labels, split):
        return tf.constant(0, dtype=tf.float32)

    return make_node_classifier(make_label_logits=make_label_logits,
                                make_edge_logits=_make_edge_list_logits,
                                make_label_pred_loss=make_no_label_loss,
                                make_edge_pred_loss=make_simple_skipgram_loss(None),
                                **kwargs)


def make_multilabel_logistic_regression(label_task_weight=0.5, regularization=0., clip=None, **kwargs):
    """ Uses the skipgram objective for relational data, and predicts labels with logistic regression
    using the skipgram embeddings as the features.

    Parameters
    ----------
    label_task_weight: the weight for the label task (between 0 and 1). By default, the label and edge
        task are weighted equally.
    clip: if not None, the value to clip the edge loss at.
    kwargs: additional arguments are forwarded to the `make_node_classifier` template.

    Returns
    -------
    A model function for simple multilabel logistic regression.
    """

    def make_label_logits(embeddings, features, mode, params):
        # actually computes 0.5 * \sum w^2, so it should just reproduce sklearn
        regularizer = tf.keras.regularizers.l2(l=0.5 * (label_task_weight * regularization))

        layer = tf.compat.v1.layers.dense(
            embeddings, params['n_labels'], activation=None, use_bias=True,
            kernel_regularizer=regularizer,
            bias_regularizer=regularizer,
            name='logits_labels')

        return layer

    edge_task_weight = 1 - label_task_weight

    return make_node_classifier(
        make_label_logits=make_label_logits,
        make_edge_logits=_make_edge_list_logits,
        make_label_pred_loss=make_weighted_loss(_make_label_sigmoid_cross_entropy_loss, label_task_weight),
        make_edge_pred_loss=make_weighted_loss(make_simple_skipgram_loss(clip), edge_task_weight),
        **kwargs)


def make_multilabel_deep_logistic_regression():
    """ Uses the skipgram objective for relational data, and predicts labels with deep logistic regression
    using the skipgram embeddings as the features

    Returns
    -------
    a function be passed to model_fn
    """

    def make_label_logits(embeddings, features, mode, params):
        for units in params['hidden_units']:
            net = tf.compat.v1.layers.dense(embeddings, units=units, activation=tf.nn.relu)

        return tf.compat.v1.layers.dense(net, params['n_labels'], activation=None)

    return make_node_classifier(make_label_logits=make_label_logits,
                                make_edge_logits=_make_edge_list_logits,
                                make_label_pred_loss=_make_label_sigmoid_cross_entropy_loss,
                                make_edge_pred_loss=make_simple_skipgram_loss(12))



#
# helper functions follow
#


def _make_label_sigmoid_cross_entropy_loss(logits, present_labels, split):
    """ Helper function to create label loss

    Parameters
    ----------
    logits: tensor of shape [batch_size, num_verts, num_labels]
    present_labels: tensor of shape [batch_size, num_verts, num_labels]; labels of labelled verts
    split: tensor of shape [batch_size, num_verts], 0 if censored, 1 if not censored

    Returns
    -------
    The cross-entropy loss corresponding to the label.
    """
    if len(logits.shape) == 3:
        batch_size = tf.cast(tf.shape(input=logits)[0], dtype=tf.float32)
    else:
        batch_size = 1

    label_pred_losses = tf.compat.v1.losses.sigmoid_cross_entropy(
        present_labels, logits=logits, weights=tf.expand_dims(split, -1), reduction=tf.compat.v1.losses.Reduction.NONE)

    # sum rather than (tf default of) mean because ¯\_(ツ)_/¯
    label_pred_loss = tf.reduce_sum(input_tensor=label_pred_losses)

    return label_pred_loss / batch_size


def make_weighted_loss(loss_fn, weight=1.0):
    """ Adapts the given loss function by multiplying by a given constant.

    Parameters
    ----------
    loss_fn: a function to create the loss
    weight: the value by which to weigh the loss.

    Returns
    -------
    fn: The adapted loss
    """
    def fn(*args, **kwargs):
        loss = loss_fn(*args, **kwargs)
        if weight != 0:
            return weight * loss
        else:
            return tf.constant(0.0, dtype=loss.dtype)

    return fn


def _make_edge_list_logits(embeddings, features, edge_list, weights, params):
    """ Helper function to create the skipgram loss for edge structure

    Parameters
    ----------
    embeddings: the embeddings features for the current subgraph.
    features: features from tensorflow dataset (not used)
    edge_list: edge list of the subgraph
    weights: weights of the edges in the subgraph
    params: other parameters

    Returns
    -------
    a tensor representing the edge prediction loss.
    """
    with tf.compat.v1.name_scope('edge_list_logits'):
        # Here I want to change this depending on the values of 
        # params["indef_ip"] and whether it is true or false
        if params["indef_ip"]:
            diag = tf.ones(int(float(params["embedding_dim"])/2),
                           dtype=tf.float32)
            dm = tf.linalg.diag(tf.concat([diag, -1*diag], 0))
            pairwise_inner_prods = tf.matmul(embeddings, 
                                             tf.matmul(embeddings, dm), transpose_b=True, name='all_edges_logit')
        else:
            pairwise_inner_prods = tf.matmul(embeddings, embeddings, transpose_b=True, name='all_edges_logit')

        if len(edge_list.shape) == 2:
            edge_list = tf.expand_dims(edge_list, axis=0)
            pairwise_inner_prods = tf.expand_dims(pairwise_inner_prods, axis=0)
            no_batch = True
        else:
            no_batch = False

        edge_list_shape = tf.shape(input=edge_list)
        batch_size = edge_list.shape[0] if edge_list.shape[0] is not None else edge_list_shape[0]
        num_edges = edge_list.shape[1] if edge_list.shape[1] is not None else edge_list_shape[1]

        batch_index = tf.tile(
            tf.expand_dims(tf.expand_dims(tf.range(batch_size), -1), -1),
            tf.stack([1, num_edges, 1]))

        edge_index = tf.concat([batch_index, edge_list], axis=-1)
        edge_logit = tf.gather_nd(pairwise_inner_prods, edge_index)

        if no_batch:
            edge_logit = tf.squeeze(edge_logit, axis=0)

        return edge_logit


def make_simple_skipgram_loss(clip=None):
    """ Makes a simple skipgram loss for edge prediction from a given edge list.

    This function takes a simple edge list and does not further modify it. In particular,
    it does not apply any transformation such as windowing or pruning.

    Parameters
    ----------
    clip: If not None, a value to clip the individual losses at.

    Returns
    -------
    loss: a function which computes the loss.
    """
    def loss(edge_logits, num_vertex, edge_list, edge_weights, params):
        with tf.compat.v1.name_scope('skipgram_loss', values=[edge_logits, edge_list, edge_weights]):
            if len(edge_list.shape) == 3:
                batch_size = tf.cast(tf.shape(input=edge_list)[0], dtype=tf.float32)
            else:
                batch_size = 1.

            edge_present = tf.cast(tf.equal(edge_weights, 1), dtype=tf.float32)

            # values of -1 in the weights indicate padded edges which should be ignored
            # in loss computation.
            edge_censored = tf.cast(tf.not_equal(edge_weights, -1), dtype=tf.float32)

            edge_pred_loss = tf.nn.sigmoid_cross_entropy_with_logits(
                labels=edge_present, logits=edge_logits)

            edge_pred_loss = edge_pred_loss * edge_censored

            if clip:
                edge_pred_loss = tf.clip_by_value(edge_pred_loss, 0, clip)

            # sum instead of (tf default of) mean because mean screws up learning rates for embeddings
            loss_value = tf.divide(tf.reduce_sum(input_tensor=edge_pred_loss), batch_size,
                                   name='skipgram_edge_loss')
        return loss_value

    return loss