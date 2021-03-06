# coding: utf-8
import logging
import os
import pickle
import sys
import time

import numpy as np
import pytest

import ray
import ray.cluster_utils
import ray.test_utils

logger = logging.getLogger(__name__)


# https://github.com/ray-project/ray/issues/6662
def test_ignore_http_proxy(shutdown_only):
    ray.init(num_cpus=1)
    os.environ["http_proxy"] = "http://example.com"
    os.environ["https_proxy"] = "http://example.com"

    @ray.remote
    def f():
        return 1

    assert ray.get(f.remote()) == 1


# https://github.com/ray-project/ray/issues/7263
def test_grpc_message_size(shutdown_only):
    ray.init(num_cpus=1)

    @ray.remote
    def bar(*a):
        return

    # 50KiB, not enough to spill to plasma, but will be inlined.
    def f():
        return np.zeros(50000, dtype=np.uint8)

    # Executes a 10MiB task spec
    ray.get(bar.remote(*[f() for _ in range(200)]))


# https://github.com/ray-project/ray/issues/7287
def test_omp_threads_set(shutdown_only):
    ray.init(num_cpus=1)
    # Should have been auto set by ray init.
    assert os.environ["OMP_NUM_THREADS"] == "1"


def test_submit_api(shutdown_only):
    ray.init(num_cpus=2, num_gpus=1, resources={"Custom": 1})

    @ray.remote
    def f(n):
        return list(range(n))

    @ray.remote
    def g():
        return ray.get_gpu_ids()

    assert f._remote([0], num_returns=0) is None
    id1 = f._remote(args=[1], num_returns=1)
    assert ray.get(id1) == [0]
    id1, id2 = f._remote(args=[2], num_returns=2)
    assert ray.get([id1, id2]) == [0, 1]
    id1, id2, id3 = f._remote(args=[3], num_returns=3)
    assert ray.get([id1, id2, id3]) == [0, 1, 2]
    assert ray.get(
        g._remote(args=[], num_cpus=1, num_gpus=1,
                  resources={"Custom": 1})) == [0]
    infeasible_id = g._remote(args=[], resources={"NonexistentCustom": 1})
    assert ray.get(g._remote()) == []
    ready_ids, remaining_ids = ray.wait([infeasible_id], timeout=0.05)
    assert len(ready_ids) == 0
    assert len(remaining_ids) == 1

    @ray.remote
    class Actor:
        def __init__(self, x, y=0):
            self.x = x
            self.y = y

        def method(self, a, b=0):
            return self.x, self.y, a, b

        def gpu_ids(self):
            return ray.get_gpu_ids()

    @ray.remote
    class Actor2:
        def __init__(self):
            pass

        def method(self):
            pass

    a = Actor._remote(
        args=[0], kwargs={"y": 1}, num_gpus=1, resources={"Custom": 1})

    a2 = Actor2._remote()
    ray.get(a2.method._remote())

    id1, id2, id3, id4 = a.method._remote(
        args=["test"], kwargs={"b": 2}, num_returns=4)
    assert ray.get([id1, id2, id3, id4]) == [0, 1, "test", 2]


def test_invalid_arguments(shutdown_only):
    ray.init(num_cpus=2)

    for opt in [np.random.randint(-100, -1), np.random.uniform(0, 1)]:
        with pytest.raises(
                ValueError,
                match="The keyword 'num_returns' only accepts 0 or a"
                " positive integer"):

            @ray.remote(num_returns=opt)
            def g1():
                return 1

    for opt in [np.random.randint(-100, -2), np.random.uniform(0, 1)]:
        with pytest.raises(
                ValueError,
                match="The keyword 'max_retries' only accepts 0, -1 or a"
                " positive integer"):

            @ray.remote(max_retries=opt)
            def g2():
                return 1

    for opt in [np.random.randint(-100, -1), np.random.uniform(0, 1)]:
        with pytest.raises(
                ValueError,
                match="The keyword 'max_calls' only accepts 0 or a positive"
                " integer"):

            @ray.remote(max_calls=opt)
            def g3():
                return 1

    for opt in [np.random.randint(-100, -2), np.random.uniform(0, 1)]:
        with pytest.raises(
                ValueError,
                match="The keyword 'max_restarts' only accepts -1, 0 or a"
                " positive integer"):

            @ray.remote(max_restarts=opt)
            class A1:
                x = 1

    for opt in [np.random.randint(-100, -2), np.random.uniform(0, 1)]:
        with pytest.raises(
                ValueError,
                match="The keyword 'max_task_retries' only accepts -1, 0 or a"
                " positive integer"):

            @ray.remote(max_task_retries=opt)
            class A2:
                x = 1


def test_many_fractional_resources(shutdown_only):
    ray.init(num_cpus=2, num_gpus=2, resources={"Custom": 2})

    @ray.remote
    def g():
        return 1

    @ray.remote
    def f(block, accepted_resources):
        true_resources = {
            resource: value[0][1]
            for resource, value in ray.get_resource_ids().items()
        }
        if block:
            ray.get(g.remote())
        return ray.test_utils.dicts_equal(true_resources, accepted_resources)

    # Check that the resource are assigned correctly.
    result_ids = []
    for rand1, rand2, rand3 in np.random.uniform(size=(100, 3)):
        resource_set = {"CPU": int(rand1 * 10000) / 10000}
        result_ids.append(f._remote([False, resource_set], num_cpus=rand1))

        resource_set = {"CPU": 1, "GPU": int(rand1 * 10000) / 10000}
        result_ids.append(f._remote([False, resource_set], num_gpus=rand1))

        resource_set = {"CPU": 1, "Custom": int(rand1 * 10000) / 10000}
        result_ids.append(
            f._remote([False, resource_set], resources={"Custom": rand1}))

        resource_set = {
            "CPU": int(rand1 * 10000) / 10000,
            "GPU": int(rand2 * 10000) / 10000,
            "Custom": int(rand3 * 10000) / 10000
        }
        result_ids.append(
            f._remote(
                [False, resource_set],
                num_cpus=rand1,
                num_gpus=rand2,
                resources={"Custom": rand3}))
        result_ids.append(
            f._remote(
                [True, resource_set],
                num_cpus=rand1,
                num_gpus=rand2,
                resources={"Custom": rand3}))
    assert all(ray.get(result_ids))

    # Check that the available resources at the end are the same as the
    # beginning.
    stop_time = time.time() + 10
    correct_available_resources = False
    while time.time() < stop_time:
        available_resources = ray.available_resources()
        if ("CPU" in available_resources
                and ray.available_resources()["CPU"] == 2.0
                and "GPU" in available_resources
                and ray.available_resources()["GPU"] == 2.0
                and "Custom" in available_resources
                and ray.available_resources()["Custom"] == 2.0):
            correct_available_resources = True
            break
    if not correct_available_resources:
        assert False, "Did not get correct available resources."


def test_background_tasks_with_max_calls(shutdown_only):
    ray.init(num_cpus=2)

    @ray.remote
    def g():
        time.sleep(.1)
        return 0

    @ray.remote(max_calls=1, max_retries=0)
    def f():
        return [g.remote()]

    nested = ray.get([f.remote() for _ in range(10)])

    # Should still be able to retrieve these objects, since f's workers will
    # wait for g to finish before exiting.
    ray.get([x[0] for x in nested])

    @ray.remote(max_calls=1, max_retries=0)
    def f():
        return os.getpid(), g.remote()

    nested = ray.get([f.remote() for _ in range(10)])
    while nested:
        pid, g_id = nested.pop(0)
        ray.get(g_id)
        del g_id
        ray.test_utils.wait_for_pid_to_exit(pid)


def test_fair_queueing(shutdown_only):
    ray.init(num_cpus=1, _system_config={"fair_queueing_enabled": 1})

    @ray.remote
    def h():
        return 0

    @ray.remote
    def g():
        return ray.get(h.remote())

    @ray.remote
    def f():
        return ray.get(g.remote())

    # This will never finish without fair queueing of {f, g, h}:
    # https://github.com/ray-project/ray/issues/3644
    ready, _ = ray.wait(
        [f.remote() for _ in range(1000)], timeout=60.0, num_returns=1000)
    assert len(ready) == 1000, len(ready)


def test_put_get(shutdown_only):
    ray.init(num_cpus=0)

    for i in range(100):
        value_before = i * 10**6
        object_ref = ray.put(value_before)
        value_after = ray.get(object_ref)
        assert value_before == value_after

    for i in range(100):
        value_before = i * 10**6 * 1.0
        object_ref = ray.put(value_before)
        value_after = ray.get(object_ref)
        assert value_before == value_after

    for i in range(100):
        value_before = "h" * i
        object_ref = ray.put(value_before)
        value_after = ray.get(object_ref)
        assert value_before == value_after

    for i in range(100):
        value_before = [1] * i
        object_ref = ray.put(value_before)
        value_after = ray.get(object_ref)
        assert value_before == value_after


@pytest.mark.skipif(sys.platform != "linux", reason="Failing on Windows")
def test_wait_timing(shutdown_only):
    ray.init(num_cpus=2)

    @ray.remote
    def f():
        time.sleep(1)

    future = f.remote()

    start = time.time()
    ready, not_ready = ray.wait([future], timeout=0.2)
    assert 0.2 < time.time() - start < 0.3
    assert len(ready) == 0
    assert len(not_ready) == 1


def test_function_descriptor():
    python_descriptor = ray._raylet.PythonFunctionDescriptor(
        "module_name", "function_name", "class_name", "function_hash")
    python_descriptor2 = pickle.loads(pickle.dumps(python_descriptor))
    assert python_descriptor == python_descriptor2
    assert hash(python_descriptor) == hash(python_descriptor2)
    assert python_descriptor.function_id == python_descriptor2.function_id
    java_descriptor = ray._raylet.JavaFunctionDescriptor(
        "class_name", "function_name", "signature")
    java_descriptor2 = pickle.loads(pickle.dumps(java_descriptor))
    assert java_descriptor == java_descriptor2
    assert python_descriptor != java_descriptor
    assert python_descriptor != object()
    d = {python_descriptor: 123}
    assert d.get(python_descriptor2) == 123


def test_ray_options(shutdown_only):
    @ray.remote(
        num_cpus=2, num_gpus=3, memory=150 * 2**20, resources={"custom1": 1})
    def foo():
        import time
        # Sleep for a heartbeat period to ensure resources changing reported.
        time.sleep(0.1)
        return ray.available_resources()

    ray.init(num_cpus=10, num_gpus=10, resources={"custom1": 2})

    without_options = ray.get(foo.remote())
    with_options = ray.get(
        foo.options(
            num_cpus=3,
            num_gpus=4,
            memory=50 * 2**20,
            resources={
                "custom1": 0.5
            }).remote())

    to_check = ["CPU", "GPU", "memory", "custom1"]
    for key in to_check:
        assert without_options[key] != with_options[key], key
    assert without_options != with_options


def test_nested_functions(ray_start_shared_local_modes):
    # Make sure that remote functions can use other values that are defined
    # after the remote function but before the first function invocation.
    @ray.remote
    def f():
        return g(), ray.get(h.remote())

    def g():
        return 1

    @ray.remote
    def h():
        return 2

    assert ray.get(f.remote()) == (1, 2)

    # Test a remote function that recursively calls itself.

    @ray.remote
    def factorial(n):
        if n == 0:
            return 1
        return n * ray.get(factorial.remote(n - 1))

    assert ray.get(factorial.remote(0)) == 1
    assert ray.get(factorial.remote(1)) == 1
    assert ray.get(factorial.remote(2)) == 2
    assert ray.get(factorial.remote(3)) == 6
    assert ray.get(factorial.remote(4)) == 24
    assert ray.get(factorial.remote(5)) == 120

    # Test remote functions that recursively call each other.

    @ray.remote
    def factorial_even(n):
        assert n % 2 == 0
        if n == 0:
            return 1
        return n * ray.get(factorial_odd.remote(n - 1))

    @ray.remote
    def factorial_odd(n):
        assert n % 2 == 1
        return n * ray.get(factorial_even.remote(n - 1))

    assert ray.get(factorial_even.remote(4)) == 24
    assert ray.get(factorial_odd.remote(5)) == 120


def test_ray_recursive_objects(ray_start_shared_local_modes):
    class ClassA:
        pass

    # Make a list that contains itself.
    lst = []
    lst.append(lst)
    # Make an object that contains itself as a field.
    a1 = ClassA()
    a1.field = a1
    # Make two objects that contain each other as fields.
    a2 = ClassA()
    a3 = ClassA()
    a2.field = a3
    a3.field = a2
    # Make a dictionary that contains itself.
    d1 = {}
    d1["key"] = d1
    # Create a list of recursive objects.
    recursive_objects = [lst, a1, a2, a3, d1]

    # Serialize the recursive objects.
    for obj in recursive_objects:
        ray.put(obj)


def test_passing_arguments_by_value_out_of_the_box(
        ray_start_shared_local_modes):
    @ray.remote
    def f(x):
        return x

    # Test passing lambdas.

    def temp():
        return 1

    assert ray.get(f.remote(temp))() == 1
    assert ray.get(f.remote(lambda x: x + 1))(3) == 4

    # Test sets.
    assert ray.get(f.remote(set())) == set()
    s = {1, (1, 2, "hi")}
    assert ray.get(f.remote(s)) == s

    # Test types.
    assert ray.get(f.remote(int)) == int
    assert ray.get(f.remote(float)) == float
    assert ray.get(f.remote(str)) == str

    class Foo:
        def __init__(self):
            pass

    # Make sure that we can put and get a custom type. Note that the result
    # won't be "equal" to Foo.
    ray.get(ray.put(Foo))


def test_putting_object_that_closes_over_object_ref(
        ray_start_shared_local_modes):
    # This test is here to prevent a regression of
    # https://github.com/ray-project/ray/issues/1317.

    class Foo:
        def __init__(self):
            self.val = ray.put(0)

        def method(self):
            f

    f = Foo()
    ray.put(f)


def test_keyword_args(ray_start_shared_local_modes):
    @ray.remote
    def keyword_fct1(a, b="hello"):
        return "{} {}".format(a, b)

    @ray.remote
    def keyword_fct2(a="hello", b="world"):
        return "{} {}".format(a, b)

    @ray.remote
    def keyword_fct3(a, b, c="hello", d="world"):
        return "{} {} {} {}".format(a, b, c, d)

    x = keyword_fct1.remote(1)
    assert ray.get(x) == "1 hello"
    x = keyword_fct1.remote(1, "hi")
    assert ray.get(x) == "1 hi"
    x = keyword_fct1.remote(1, b="world")
    assert ray.get(x) == "1 world"
    x = keyword_fct1.remote(a=1, b="world")
    assert ray.get(x) == "1 world"

    x = keyword_fct2.remote(a="w", b="hi")
    assert ray.get(x) == "w hi"
    x = keyword_fct2.remote(b="hi", a="w")
    assert ray.get(x) == "w hi"
    x = keyword_fct2.remote(a="w")
    assert ray.get(x) == "w world"
    x = keyword_fct2.remote(b="hi")
    assert ray.get(x) == "hello hi"
    x = keyword_fct2.remote("w")
    assert ray.get(x) == "w world"
    x = keyword_fct2.remote("w", "hi")
    assert ray.get(x) == "w hi"

    x = keyword_fct3.remote(0, 1, c="w", d="hi")
    assert ray.get(x) == "0 1 w hi"
    x = keyword_fct3.remote(0, b=1, c="w", d="hi")
    assert ray.get(x) == "0 1 w hi"
    x = keyword_fct3.remote(a=0, b=1, c="w", d="hi")
    assert ray.get(x) == "0 1 w hi"
    x = keyword_fct3.remote(0, 1, d="hi", c="w")
    assert ray.get(x) == "0 1 w hi"
    x = keyword_fct3.remote(0, 1, c="w")
    assert ray.get(x) == "0 1 w world"
    x = keyword_fct3.remote(0, 1, d="hi")
    assert ray.get(x) == "0 1 hello hi"
    x = keyword_fct3.remote(0, 1)
    assert ray.get(x) == "0 1 hello world"
    x = keyword_fct3.remote(a=0, b=1)
    assert ray.get(x) == "0 1 hello world"

    # Check that we cannot pass invalid keyword arguments to functions.
    @ray.remote
    def f1():
        return

    @ray.remote
    def f2(x, y=0, z=0):
        return

    # Make sure we get an exception if too many arguments are passed in.
    with pytest.raises(Exception):
        f1.remote(3)

    with pytest.raises(Exception):
        f1.remote(x=3)

    with pytest.raises(Exception):
        f2.remote(0, w=0)

    with pytest.raises(Exception):
        f2.remote(3, x=3)

    # Make sure we get an exception if too many arguments are passed in.
    with pytest.raises(Exception):
        f2.remote(1, 2, 3, 4)

    @ray.remote
    def f3(x):
        return x

    assert ray.get(f3.remote(4)) == 4


def test_args_starkwargs(ray_start_shared_local_modes):
    def starkwargs(a, b, **kwargs):
        return a, b, kwargs

    class TestActor:
        def starkwargs(self, a, b, **kwargs):
            return a, b, kwargs

    def test_function(fn, remote_fn):
        assert fn(1, 2, x=3) == ray.get(remote_fn.remote(1, 2, x=3))
        with pytest.raises(TypeError):
            remote_fn.remote(3)

    remote_test_function = ray.remote(test_function)

    remote_starkwargs = ray.remote(starkwargs)
    test_function(starkwargs, remote_starkwargs)
    ray.get(remote_test_function.remote(starkwargs, remote_starkwargs))

    remote_actor_class = ray.remote(TestActor)
    remote_actor = remote_actor_class.remote()
    actor_method = remote_actor.starkwargs
    local_actor = TestActor()
    local_method = local_actor.starkwargs
    test_function(local_method, actor_method)
    ray.get(remote_test_function.remote(local_method, actor_method))


def test_args_named_and_star(ray_start_shared_local_modes):
    def hello(a, x="hello", **kwargs):
        return a, x, kwargs

    class TestActor:
        def hello(self, a, x="hello", **kwargs):
            return a, x, kwargs

    def test_function(fn, remote_fn):
        assert fn(1, x=2, y=3) == ray.get(remote_fn.remote(1, x=2, y=3))
        assert fn(1, 2, y=3) == ray.get(remote_fn.remote(1, 2, y=3))
        assert fn(1, y=3) == ray.get(remote_fn.remote(1, y=3))

        assert fn(1, ) == ray.get(remote_fn.remote(1, ))
        assert fn(1) == ray.get(remote_fn.remote(1))

        with pytest.raises(TypeError):
            remote_fn.remote(1, 2, x=3)

    remote_test_function = ray.remote(test_function)

    remote_hello = ray.remote(hello)
    test_function(hello, remote_hello)
    ray.get(remote_test_function.remote(hello, remote_hello))

    remote_actor_class = ray.remote(TestActor)
    remote_actor = remote_actor_class.remote()
    actor_method = remote_actor.hello
    local_actor = TestActor()
    local_method = local_actor.hello
    test_function(local_method, actor_method)
    ray.get(remote_test_function.remote(local_method, actor_method))


def test_args_stars_after(ray_start_shared_local_modes):
    def star_args_after(a="hello", b="heo", *args, **kwargs):
        return a, b, args, kwargs

    class TestActor:
        def star_args_after(self, a="hello", b="heo", *args, **kwargs):
            return a, b, args, kwargs

    def test_function(fn, remote_fn):
        assert fn("hi", "hello", 2) == ray.get(
            remote_fn.remote("hi", "hello", 2))
        assert fn(
            "hi", "hello", 2, hi="hi") == ray.get(
                remote_fn.remote("hi", "hello", 2, hi="hi"))
        assert fn(hi="hi") == ray.get(remote_fn.remote(hi="hi"))

    remote_test_function = ray.remote(test_function)

    remote_star_args_after = ray.remote(star_args_after)
    test_function(star_args_after, remote_star_args_after)
    ray.get(
        remote_test_function.remote(star_args_after, remote_star_args_after))

    remote_actor_class = ray.remote(TestActor)
    remote_actor = remote_actor_class.remote()
    actor_method = remote_actor.star_args_after
    local_actor = TestActor()
    local_method = local_actor.star_args_after
    test_function(local_method, actor_method)
    ray.get(remote_test_function.remote(local_method, actor_method))


def test_object_id_backward_compatibility(ray_start_shared_local_modes):
    # We've renamed Python's `ObjectID` to `ObjectRef`, and added a type
    # alias for backward compatibility.
    # This test is to make sure legacy code can still use `ObjectID`.
    # TODO(hchen): once we completely remove Python's `ObjectID`,
    # this test can be removed as well.

    # Check that these 2 types are the same.
    assert ray.ObjectID == ray.ObjectRef
    object_ref = ray.put(1)
    # Check that users can use either type in `isinstance`
    assert isinstance(object_ref, ray.ObjectID)
    assert isinstance(object_ref, ray.ObjectRef)


def test_nonascii_in_function_body(ray_start_shared_local_modes):
    @ray.remote
    def return_a_greek_char():
        return "??"

    assert ray.get(return_a_greek_char.remote()) == "??"


if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
