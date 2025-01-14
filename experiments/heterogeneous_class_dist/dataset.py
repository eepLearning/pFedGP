import random
from collections import defaultdict
import pickle
from pathlib import Path
import numpy as np
import torch.utils.data
import torchvision.transforms as transforms
from torchvision.datasets import CIFAR10, CIFAR100
import torchvision
import os

def get_datasets(data_name, dataroot, normalize=True, val_size=10000):
    """
    get_datasets returns train/val/test data splits of CIFAR10/100 datasets
    :param data_name: name of datafolder, choose from [cifar10, cifar100]
    :param dataroot: root to data dir
    :param normalize: True/False to normalize the data
    :param val_size: validation split size (in #samples)
    :return: train_set, val_set, test_set (tuple of pytorch datafolder/subset)
    """

    norm_map = {
        "cifar10": [
            transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
            CIFAR10
        ],
        "cifar100": [
            transforms.Normalize((0.5071, 0.4865, 0.4409), (0.2673, 0.2564, 0.2762)),
            CIFAR100
        ]
    }
    if "cifar" in data_name:
        normalization, data_obj = norm_map[data_name]

        trans = [transforms.ToTensor()]

        if normalize:
            trans.append(normalization)

        transform = transforms.Compose(trans)

        dataset = data_obj(
            dataroot,
            train=True,
            download=True,
            transform=transform
        )

        test_set = data_obj(
            dataroot,
            train=False,
            download=True,
            transform=transform
        )

        train_size = len(dataset) - val_size
        train_set, val_set = torch.utils.data.random_split(dataset, [train_size, val_size])

    elif data_name == 'cinic10':
        #TODO: To fix the address for normal case
        if not os.path.exists("../cinic/train"):
            raise ValueError("Cinic Dataset")
        cinic_mean = [0.47889522, 0.47227842, 0.43047404]
        cinic_std = [0.24205776, 0.23828046, 0.25874835]
        normalization = transforms.Normalize(cinic_mean, cinic_std)
        cinic_trans = [transforms.ToTensor()]
        if normalize:
          cinic_trans.append(normalization)

        cinic_transform = transforms.Compose(cinic_trans)
        print("Function get_dataset Call ")

        train_set= torchvision.datasets.ImageFolder('../cinic/train',transform=cinic_transform)
        val_set = torchvision.datasets.ImageFolder('../cinic/valid', transform=cinic_transform)
        test_set = torchvision.datasets.ImageFolder('../cinic/test', transform=cinic_transform)
        #train_set, val_set, test_set = get_cinic_dataset(dataroot)

    else:
        raise ValueError("choose data_name from ['cifar10', 'cifar100', 'cinic10]")

    return train_set, val_set, test_set


def get_num_classes_samples(dataset):
    """
    extracts info about certain datafolder
    :param dataset: pytorch datafolder object
    :return: datafolder info number of classes, number of samples, list of labels
    """
    # ---------------#
    # Extract labels #
    # ---------------#
    if hasattr(dataset, "targets"):
        if isinstance(dataset.targets, list):
            data_labels_list = np.array(dataset.targets)
        else:
            data_labels_list = dataset.targets
    elif hasattr(dataset, "dataset"):
        if isinstance(dataset.dataset.targets, list):
            data_labels_list = np.array(dataset.dataset.targets)[dataset.indices]
        else:
            data_labels_list = dataset.dataset.targets[dataset.indices]
    else:  # tensorDataset Object
        data_labels_list = np.array(dataset.tensors[1])
    classes, num_samples = np.unique(data_labels_list, return_counts=True)
    num_classes = len(classes)
    return num_classes, num_samples, data_labels_list


def gen_classes_per_node(dataset, num_users, classes_per_user=2, high_prob=0.6, low_prob=0.4):
    """
    creates the data distribution of each client
    :param dataset: pytorch datafolder object
    :param num_users: number of clients
    :param classes_per_user: number of classes assigned to each client
    :param high_prob: highest prob sampled
    :param low_prob: lowest prob sampled
    :return: dictionary mapping between classes and proportions, each entry refers to other client
    """
    num_classes, num_samples, _ = get_num_classes_samples(dataset)

    # -------------------------------------------#
    # Divide classes + num samples for each user #
    # -------------------------------------------#
    assert (classes_per_user * num_users) % num_classes == 0, "equal classes appearance is needed"
    count_per_class = (classes_per_user * num_users) // num_classes
    class_dict = {}
    for i in range(num_classes):
        # sampling alpha_i_c
        probs = np.random.uniform(low_prob, high_prob, size=count_per_class)
        # normalizing
        probs_norm = (probs / probs.sum()).tolist()
        class_dict[i] = {'count': count_per_class, 'prob': probs_norm}

    # -------------------------------------#
    # Assign each client with data indexes #
    # -------------------------------------#
    class_partitions = defaultdict(list)
    for i in range(num_users):
        c = []
        for _ in range(classes_per_user):
            class_counts = [class_dict[i]['count'] for i in range(num_classes)]
            max_class_counts = np.where(np.array(class_counts) == max(class_counts))[0]
            # avoid selected classes
            max_class_counts = list(set(max_class_counts) - set(c))
            c.append(np.random.choice(max_class_counts))
            class_dict[c[-1]]['count'] -= 1
        class_partitions['class'].append(c)
        class_partitions['prob'].append([class_dict[i]['prob'].pop() for i in c])
    return class_partitions


def gen_data_split(dataset, num_users, class_partitions):
    """
    divide data indexes for each client based on class_partition
    :param dataset: pytorch datafolder object (train/val/test)
    :param num_users: number of clients
    :param class_partitions: proportion of classes per client
    :return: dictionary mapping client to its indexes
    """
    num_classes, num_samples, data_labels_list = get_num_classes_samples(dataset)

    # -------------------------- #
    # Create class index mapping #
    # -------------------------- #
    data_class_idx = {i: np.where(data_labels_list == i)[0] for i in range(num_classes)}

    # --------- #
    # Shuffling #
    # --------- #
    for data_idx in data_class_idx.values():
        random.shuffle(data_idx)

    # ------------------------------ #
    # Assigning samples to each user #
    # ------------------------------ #
    user_data_idx = [[] for i in range(num_users)]
    for usr_i in range(num_users):
        for c, p in zip(class_partitions['class'][usr_i], class_partitions['prob'][usr_i]):
            end_idx = int(num_samples[c] * p)
            user_data_idx[usr_i].extend(data_class_idx[c][:end_idx])
            data_class_idx[c] = data_class_idx[c][end_idx:]

    return user_data_idx


def gen_random_loaders(data_name, data_path, num_users, bz, classes_per_user, normalize=True):
    """
    generates train/val/test loaders of each client
    :param data_name: name of datafolder, choose from [cifar10, cifar100]
    :param data_path: root path for data dir
    :param num_users: number of clients
    :param bz: batch size
    :param classes_per_user: number of classes assigned to each client
    :return: train/val/test loaders of each client, list of pytorch dataloaders
    """
    loader_params = {"batch_size": bz, "shuffle": False, "pin_memory": True, "num_workers": 4}
    dataloaders = []
    datasets = get_datasets(data_name, data_path, normalize=normalize)

    for i, d in enumerate(datasets):
        # ensure same partition for train/test/val
        if i == 0:
            cls_partitions = gen_classes_per_node(d, num_users, classes_per_user)
            loader_params['shuffle'] = True
        usr_subset_idx = gen_data_split(d, num_users, cls_partitions)
        # create subsets for each client
        subsets = list(map(lambda x: torch.utils.data.Subset(d, x), usr_subset_idx))
        # create dataloaders from subsets
        dataloaders.append(list(map(lambda x: torch.utils.data.DataLoader(x, **loader_params), subsets)))
        # do not shuffle at eval and test
        loader_params['shuffle'] = False

    return dataloaders


def get_dataset_split(pkl_path, split):
    if not isinstance(pkl_path, Path):
        pkl_path = Path(pkl_path)
    data = []
    for i in ("x", "y"):
        file = pkl_path / "_".join([i, split, "dataset.pkl"])
        with open(file, "rb") as file:
            data.append(pickle.load(file))
    x, y = data
    x = x / 255.0
    x = torch.from_numpy(x.astype(np.float32)).permute(0, 3, 1, 2)
    y = torch.from_numpy(y.astype(np.long))
    dataset = torch.utils.data.TensorDataset(x, y)
    return dataset


def get_cinic_dataset(pkl_path):
    datasets = []
    for split in ("train", "valid", "test"):
        datasets.append(get_dataset_split(pkl_path, split))
    return datasets
